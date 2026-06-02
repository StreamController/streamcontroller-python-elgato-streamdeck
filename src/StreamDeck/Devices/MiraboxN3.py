#         Python Stream Deck Library
#      Released under the MIT license
#
#   Mirabox StreamDock N3 / AJAZZ AKP03 adapter
#   Protocol implementation based on mirajazz (Rust) as absolute reference:
#     https://github.com/4ndv/mirajazz
#
#   Protocol version 2: 1024-byte packets, no both-states (PIDs starting with 1)
#   Protocol version 3: 1024-byte packets, supports both key states (PIDs starting with 3)

import threading
import time

from .StreamDeck import StreamDeck, ControlType, DialEventType


# PIDs that use protocol version 3 (supports both keypress states up/down)
_PROTOCOL_V3_PIDS = {
    0x3002,  # AKP03E rev.2
    0x3003,  # AKP03R rev.2
}


class MiraboxN3(StreamDeck):
    """
    Represents a physically attached Mirabox StreamDock N3 or AJAZZ AKP03 device.

    Hardware layout:
      - 6 LCD keys (2 rows × 3 cols), key image 60×60 px JPEG
      - 3 side buttons
      - 3 rotary encoders with press
      - HID interface: usage_page=0xFFA0 (vendor-specific command channel)
      - HID report: Output=1025 bytes (1 byte report-id + 1024 payload)

    Protocol reference: mirajazz v0.14.0 (https://github.com/4ndv/mirajazz)
    """

    # --- Class-level constants (StreamDeck contract) ---

    KEY_COUNT = 6         # LCD keys only (the 6 main keys with images)
    KEY_COLS = 3
    KEY_ROWS = 2

    TOUCH_KEY_COUNT = 3   # Side buttons (reported as keys index 6,7,8)

    KEY_PIXEL_WIDTH = 60
    KEY_PIXEL_HEIGHT = 60
    KEY_IMAGE_FORMAT = "JPEG"
    KEY_FLIP = (False, False)
    KEY_ROTATION = 270

    DIAL_COUNT = 3

    DECK_TYPE = "Mirabox StreamDock N3"
    DECK_VISUAL = True
    DECK_TOUCH = False

    # --- Protocol constants (from mirajazz device.rs) ---

    PACKET_SIZE = 1024    # Payload size (not counting report-id byte)

    # Event code mappings (from mirajazz examples and OpenDeck plugins)
    # LCD keys: byte 9 values 0x01..0x06, byte 10: 0x01=press, 0x00=release (pv3)
    # Side buttons: byte 9 values 0x25, 0x30, 0x31 (press-only on pv2, both on pv3)
    # Encoder press: byte 9 values 0x33, 0x34, 0x35
    # Encoder rotate CCW: 0x90, 0x60, 0x50
    # Encoder rotate CW: 0x91, 0x61, 0x51

    _SIDE_BUTTON_MAP = {
        0x25: 6,  # side button left  → key index 6
        0x30: 7,  # side button center → key index 7
        0x31: 8,  # side button right  → key index 8
    }

    _ENCODER_PRESS_MAP = {
        0x33: 0,  # bottom-left encoder → dial index 0
        0x34: 1,  # bottom-right encoder → dial index 1
        0x35: 2,  # top encoder → dial index 2
    }

    _ENCODER_ROTATE_MAP = {
        0x90: (0, -1),  # bottom-left CCW → dial 0, amount -1
        0x91: (0, +1),  # bottom-left CW  → dial 0, amount +1
        0x60: (1, -1),  # bottom-right CCW → dial 1, amount -1
        0x61: (1, +1),  # bottom-right CW  → dial 1, amount +1
        0x50: (2, -1),  # top CCW → dial 2, amount -1
        0x51: (2, +1),  # top CW  → dial 2, amount +1
    }

    # ACK prefix: mirajazz checks only the first 3 bytes [0x41, 0x43, 0x4B] = "ACK"
    _ACK_PREFIX = bytes([0x41, 0x43, 0x4B])  # "ACK"

    def __init__(self, device):
        super().__init__(device)
        self._keepalive_thread = None
        self._run_keepalive = False
        self._initialized = False
        self._firmware_version = None

        # Determine protocol version from PID
        pid = device.product_id() if hasattr(device, 'product_id') else 0
        self._protocol_version = 3 if pid in _PROTOCOL_V3_PIDS else 2

    # ─── Internal protocol helpers (mirajazz device.rs) ───────────────

    def _make_packet(self, payload_data):
        """
        Build a full HID output report: 1 byte report-id (0x00) + 1024 bytes payload.
        Total = 1025 bytes, matching mirajazz write_extended_data().

        Reference: mirajazz device.rs line 651-655
        """
        packet = bytearray(self.PACKET_SIZE + 1)  # 1025 bytes total
        packet[0] = 0x00  # report-id
        end = min(len(payload_data), self.PACKET_SIZE)
        packet[1:1 + end] = payload_data[:end]
        return packet

    def _send_command(self, cmd_bytes):
        """
        Send a CRT command as a full padded HID packet.
        The command is prefixed with CRT\\0\\0 and padded to 1025 bytes.

        Reference: mirajazz device.rs write_extended_data()
        """
        # Build payload: CRT\0\0 + command
        payload = bytearray([0x43, 0x52, 0x54, 0x00, 0x00]) + bytearray(cmd_bytes)
        self.device.write(self._make_packet(payload))

    def _send_stp(self):
        """
        Send STP command to commit pending operations (image transfers, clears).
        Required for protocol v2+ after BAT and CLE commands.

        Reference: mirajazz device.rs line 580
        """
        self._send_command(bytes([0x53, 0x54, 0x50]))  # "STP"

    # ─── Initialization (mirajazz device.rs line 351-367) ─────────────

    def _initialize_device(self):
        """
        Performs the mirajazz lazy initialization sequence.
        Called once before the first command that needs the device active.

        Sends:
          1. CRT\\0\\0DIS  (display init)
          2. CRT\\0\\0LIG\\0\\0\\0\\0  (brightness init with value 0)

        Reference: mirajazz device.rs initialize(), lines 351-367
        """
        if self._initialized:
            return

        self._initialized = True

        # Command 1: DIS
        self._send_command(bytes([0x44, 0x49, 0x53]))  # "DIS"

        # Command 2: LIG with brightness=0
        self._send_command(bytes([0x4C, 0x49, 0x47, 0x00, 0x00, 0x00, 0x00]))  # "LIG\0\0\0\0"

    # ─── Keep-alive (mirajazz device.rs line 538-549) ─────────────────

    def _keepalive_worker(self):
        """
        Sends CRT CONNECT packets periodically to keep the device alive.

        Reference: mirajazz device.rs keep_alive(), lines 538-549
        The command is "CONNECT" = [0x43, 0x4F, 0x4E, 0x4E, 0x45, 0x43, 0x54]
        """
        time.sleep(1.0)  # Initial delay for device stabilization
        while self._run_keepalive:
            try:
                self._initialize_device()
                # "CONNECT"
                self._send_command(bytes([
                    0x43, 0x4F, 0x4E, 0x4E, 0x45, 0x43, 0x54
                ]))
            except Exception:
                pass
            # Sleep in small increments so we can stop quickly
            for _ in range(100):
                if not self._run_keepalive:
                    break
                time.sleep(0.1)

    def _start_keepalive(self):
        """Start the keep-alive background thread."""
        self._stop_keepalive()
        self._run_keepalive = True
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_worker, daemon=True
        )
        self._keepalive_thread.start()

    def _stop_keepalive(self):
        """Stop the keep-alive background thread."""
        self._run_keepalive = False
        if self._keepalive_thread is not None:
            try:
                self._keepalive_thread.join(timeout=2.0)
            except RuntimeError:
                pass
            self._keepalive_thread = None

    # ─── Lifecycle overrides ──────────────────────────────────────────

    def open(self, resume_from_suspend=True):
        """
        Opens the device and performs initialization following mirajazz's
        pattern (lazy init + reset sequence).

        Reference: mirajazz examples/akp03r.rs
          1. connect (opens HID)
          2. set_brightness (triggers lazy init)
          3. clear_all_button_images
          4. Start keep-alive
        """
        self.device.open()
        self.reconnect_after_suspend = resume_from_suspend

        # Reset internal state
        self._initialized = False

        if resume_from_suspend:
            self._setup_reader(self._read_with_resume_from_suspend)
        else:
            self._setup_reader(self._read)

        # Initialize + set brightness (triggers lazy init inside)
        self.set_brightness(100)

        # Clear all keys with STP commit
        self._clear_all_keys()

        # Start keep-alive (CONNECT every 10 seconds)
        self._start_keepalive()

    def close(self):
        """
        Closes the device following mirajazz shutdown sequence.

        Reference: mirajazz device.rs shutdown(), lines 552-564
          1. CRT\\0\\0CLE\\0\\0DC  (clear + disconnect code)
          2. CRT\\0\\0HAN  (hang/sleep)
        """
        self._stop_keepalive()
        self.run_read_thread = False

        try:
            # shutdown step 1: CLE\0\0DC
            self._send_command(bytes([
                0x43, 0x4C, 0x45, 0x00, 0x00, 0x44, 0x43
            ]))
            # shutdown step 2: HAN
            self._send_command(bytes([0x48, 0x41, 0x4E]))  # "HAN"
        except Exception:
            pass

        self.device.close()

    # ─── Abstract method implementations ──────────────────────────────

    def _trigger_momentary_key(self, key_index):
        """Simulates a press-and-release cycle for a one-shot key event (PV2 side buttons)."""
        with self.update_lock:
            self.last_key_states[key_index] = True
            if self.key_callback is not None:
                try:
                    self.key_callback(self, key_index, True)
                except Exception:
                    pass

        def release_key():
            with self.update_lock:
                if self.last_key_states[key_index]:
                    self.last_key_states[key_index] = False
                    if self.key_callback is not None:
                        try:
                            self.key_callback(self, key_index, False)
                        except Exception:
                            pass

        threading.Timer(0.05, release_key).start()

    def _trigger_momentary_dial(self, dial_index):
        """Simulates a press-and-release cycle for a one-shot dial event (PV2 encoders)."""
        with self.update_lock:
            self.last_dial_states[dial_index] = True
            if self.dial_callback is not None:
                try:
                    self.dial_callback(self, dial_index, DialEventType.PUSH, True)
                except Exception:
                    pass

        def release_dial():
            with self.update_lock:
                if self.last_dial_states[dial_index]:
                    self.last_dial_states[dial_index] = False
                    if self.dial_callback is not None:
                        try:
                            self.dial_callback(self, dial_index, DialEventType.PUSH, False)
                        except Exception:
                            pass

        threading.Timer(0.05, release_dial).start()

    def _read_control_states(self):
        """
        Reads and parses a single HID input report from the device.

        Protocol (from mirajazz state.rs line 84-115):
          - Read 512 bytes from input endpoint
          - Check for ACK prefix (3 bytes: 0x41, 0x43, 0x4B)
          - Event code at data[9], state at data[10]
          - For pv3: data[10] has 0x01=press, 0x00=release
          - For pv2: only press events, state is always 0x01

        Returns one of:
          - {ControlType.KEY: [bool, ...]}           for key/button events
          - {ControlType.DIAL: {DialEventType: [..]}} for encoder events
          - None if no data or unrecognized event
        """
        total_keys = self.KEY_COUNT + self.TOUCH_KEY_COUNT  # 6 + 3 = 9

        # Read a raw report from the device (512 bytes, matching mirajazz)
        data = self.device.read(512)
        if data is None:
            return None

        # Validate ACK prefix (mirajazz state.rs line 104)
        # Only first 3 bytes: [0x41, 0x43, 0x4B] = "ACK"
        if len(data) < 11:
            return None

        if data[:3] != self._ACK_PREFIX:
            return None

        event_code = data[9]
        event_state = data[10]

        # --- LCD Keys (0x01..0x06) ---
        if 0x01 <= event_code <= 0x06:
            states = [False] * total_keys
            key_index = event_code - 1  # 0-based
            states[key_index] = (event_state == 0x01)
            return {ControlType.KEY: states}

        # --- Side buttons (0x25, 0x30, 0x31) ---
        if event_code in self._SIDE_BUTTON_MAP:
            key_index = self._SIDE_BUTTON_MAP[event_code]
            if self._protocol_version == 2:
                # PV2 only reports one-shot press events
                if event_state == 0x01:
                    self._trigger_momentary_key(key_index)
                return None
            else:
                # PV3 supports native release events
                states = [False] * total_keys
                states[key_index] = (event_state == 0x01)
                return {ControlType.KEY: states}

        # --- Encoder rotation (0x90/91, 0x60/61, 0x50/51) ---
        if event_code in self._ENCODER_ROTATE_MAP:
            dial_index, amount = self._ENCODER_ROTATE_MAP[event_code]
            values = [0] * self.DIAL_COUNT
            values[dial_index] = amount
            return {
                ControlType.DIAL: {
                    DialEventType.TURN: values,
                }
            }

        # --- Encoder press (0x33, 0x34, 0x35) ---
        if event_code in self._ENCODER_PRESS_MAP:
            dial_index = self._ENCODER_PRESS_MAP[event_code]
            if self._protocol_version == 2:
                # PV2 only reports one-shot press events
                if event_state == 0x01:
                    self._trigger_momentary_dial(dial_index)
                return None
            else:
                # PV3 supports native release events
                values = [False] * self.DIAL_COUNT
                values[dial_index] = (event_state == 0x01)
                return {
                    ControlType.DIAL: {
                        DialEventType.PUSH: values,
                    }
                }

        return None

    def _reset_key_stream(self):
        """
        Resets the device following mirajazz reset() pattern.

        Reference: mirajazz device.rs reset(), lines 370-377
        """
        self._initialize_device()
        self.set_brightness(100)
        self._clear_all_keys()

    def _clear_all_keys(self):
        """
        Clear all key images from the device.

        Reference: mirajazz device.rs clear_all_button_images(), lines 496-509
          1. CLE with key_id=0xFF (clear all)
          2. STP to commit (required for protocol v2+)
        """
        self._initialize_device()

        # CLE\0\0\0\xFF
        self._send_command(bytes([
            0x43, 0x4C, 0x45,   # "CLE"
            0x00, 0x00, 0x00,
            0xFF                 # 0xFF = all keys
        ]))

        # STP to commit (protocol v2+ requirement)
        self._send_stp()

    def reset(self):
        """Reset the device to its default state."""
        self._reset_key_stream()

    def set_brightness(self, percent):
        """
        Set the global screen brightness (0-100).

        Reference: mirajazz device.rs set_brightness(), lines 380-392
        Packet: CRT\\0\\0LIG\\0\\0<percent>
        """
        self._initialize_device()

        if isinstance(percent, float):
            percent = int(100.0 * percent)
        percent = min(max(percent, 0), 100)

        # "LIG\0\0<percent>"
        self._send_command(bytes([
            0x4C, 0x49, 0x47,   # "LIG"
            0x00, 0x00,
            percent,
        ]))

    def get_serial_number(self):
        """Return the device serial number from the HID descriptor."""
        return self.device.serial_number()

    def get_firmware_version(self):
        """
        Read the firmware version via HID feature report.

        Reference: mirajazz device.rs read_firmware_version_from_raw_device(), lines 307-318
        Uses feature report with report_id=0x01.
        """
        if self._firmware_version is not None:
            return self._firmware_version

        try:
            report = self.device.read_feature(0x01, 20)
            if report:
                version_str = bytes(report).split(b'\x00')[0].decode('ascii', 'replace').strip()
                if version_str:
                    self._firmware_version = version_str
                    return version_str
        except Exception:
            pass

        return ""

    def set_key_image(self, key, image):
        """
        Sets the image on a specific LCD key.

        Protocol (from mirajazz device.rs send_image + flush):
          1. Send BAT header: CRT\\0\\0BAT\\0\\0<size_hi><size_lo><key+1>
          2. Stream JPEG data in 1024-byte chunks (each in a 1025-byte packet)
          3. Send STP to commit

        Reference: mirajazz device.rs send_image(), lines 430-453
                   mirajazz device.rs flush(), lines 567-586

        :param int key: Key index (0-5 for LCD keys)
        :param bytes image: JPEG image data (60x60), or None for blank
        """
        if key < 0 or key >= self.KEY_COUNT:
            raise IndexError("Invalid key index {}.".format(key))

        self._initialize_device()

        if image is None:
            # Clear single key
            self._send_command(bytes([
                0x43, 0x4C, 0x45,   # "CLE"
                0x00, 0x00, 0x00,
                key + 1             # 1-based key ID
            ]))
            self._send_stp()
            return

        image = bytes(image)

        # Key IDs on the wire are 1-based (0x01..0x06)
        hw_key_id = key + 1

        # BAT header: CRT\0\0BAT\0\0<size_hi><size_lo><key_id>
        # Reference: mirajazz device.rs line 431-448
        bat_cmd = bytes([
            0x42, 0x41, 0x54,               # "BAT"
            0x00, 0x00,                      # padding
            (len(image) >> 8) & 0xFF,        # size high byte
            len(image) & 0xFF,               # size low byte
            hw_key_id,                       # 1-based key index
        ])
        self._send_command(bat_cmd)

        # Stream image data in 1024-byte chunks
        # Reference: mirajazz device.rs write_image_data_reports(), lines 610-638
        offset = 0
        bytes_remaining = len(image)
        while bytes_remaining > 0:
            chunk_size = min(bytes_remaining, self.PACKET_SIZE)
            chunk = image[offset:offset + chunk_size]
            self.device.write(self._make_packet(chunk))
            offset += chunk_size
            bytes_remaining -= chunk_size

        # STP to commit the image transfer
        # Reference: mirajazz device.rs flush(), line 580
        self._send_stp()

    def set_touchscreen_image(self, image, x_pos=0, y_pos=0, width=0, height=0):
        """Not applicable for N3 — no touchscreen. Silently ignored."""
        pass

    def set_key_color(self, key, r, g, b):
        """Not applicable for N3 — no RGB LEDs. Silently ignored."""
        pass

    def set_screen_image(self, image):
        """
        Sets the background/screen image.
        Not implemented in mirajazz for AKP03 — silently ignored.
        """
        pass
