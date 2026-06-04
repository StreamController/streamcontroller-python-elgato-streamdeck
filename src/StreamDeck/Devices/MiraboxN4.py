#         Python Stream Deck Library
#      Released under the MIT license
#
#   Mirabox StreamDock N4 / AJAZZ AKP05 adapter
#   Protocol implementation based on the official C++ StreamDock-Device-SDK.

import threading
import time
from .StreamDeck import StreamDeck, ControlType, DialEventType

class MiraboxN4(StreamDeck):
    """
    Represents a physically attached Mirabox StreamDock N4 device.
    """

    KEY_COUNT = 10
    KEY_COLS = 5
    KEY_ROWS = 2
    TOUCH_KEY_COUNT = 4

    KEY_PIXEL_WIDTH = 112
    KEY_PIXEL_HEIGHT = 112
    KEY_IMAGE_FORMAT = "JPEG"
    KEY_FLIP = (False, False)
    KEY_ROTATION = 180

    TOUCHBAR_KEY_PIXEL_WIDTH = 176
    TOUCHBAR_KEY_PIXEL_HEIGHT = 112
    SECOND_SCREEN_IMAGE_FORMAT = "JPEG"
    SECOND_SCREEN_ROTATION = 180

    SCREEN_PIXEL_WIDTH = 800
    SCREEN_PIXEL_HEIGHT = 480
    SCREEN_IMAGE_FORMAT = "JPEG"
    SCREEN_ROTATION = 180

    TOUCHBAR_BG_PIXEL_WIDTH = 800
    TOUCHBAR_BG_PIXEL_HEIGHT = 112
    DIAL_COUNT = 4
    JPEG_QUALITY = 90
    RECOMPRESS_KEY_IMAGES = True

    DECK_TYPE = "Mirabox StreamDeck N4"
    DECK_VISUAL = True
    DECK_TOUCH = True
    PACKET_SIZE = 1024

    _LCD_KEY_HW_MAP = {
        0x01: 0, 0x02: 1, 0x03: 2, 0x04: 3, 0x05: 4,
        0x06: 5, 0x07: 6, 0x08: 7, 0x09: 8, 0x0A: 9,
    }

    _KEY_TO_HW_ID = {
        0: 11, 1: 12, 2: 13, 3: 14, 4: 15,
        5: 6,  6: 7,  7: 8,  8: 9,  9: 10,
    }

    _SECOND_SCREEN_MAP = {
        0x40: 10, 0x41: 11, 0x42: 12, 0x43: 13,
    }

    _SECOND_SCREEN_TO_HW_ID = {
        10: 1, 11: 2, 12: 3, 13: 4,
    }

    _ENCODER_PRESS_MAP = {
        0x37: 0, 0x35: 1, 0x33: 2, 0x36: 3,
    }

    _ENCODER_ROTATE_MAP = {
        0xA0: (0, -1), 0xA1: (0, +1),
        0x50: (1, -1), 0x51: (1, +1),
        0x90: (2, -1), 0x91: (2, +1),
        0x70: (3, -1), 0x71: (3, +1),
    }

    _SWIPE_MAP = {
        0x38: "left",
        0x39: "right",
    }

    _ACK_PREFIX = bytes([0x41, 0x43, 0x4B])

    def __init__(self, device):
        super().__init__(device)
        self._keepalive_thread = None
        self._run_keepalive = False
        self._initialized = False
        self._firmware_version = None
        self._write_lock = threading.RLock()

    def deck_type(self):
        return "Mirabox StreamDeck N4"

    def is_touch(self) -> bool:
        return True

    def has_background_image(self) -> bool:
        return False

    def _make_packet(self, payload_data):
        packet = bytearray(self.PACKET_SIZE + 1)
        packet[0] = 0x00
        end = min(len(payload_data), self.PACKET_SIZE)
        packet[1:1 + end] = payload_data[:end]
        return packet

    def _send_command(self, cmd_bytes):
        payload = bytearray([0x43, 0x52, 0x54, 0x00, 0x00]) + bytearray(cmd_bytes)
        packet = self._make_packet(payload)
        with self._write_lock:
            for _ in range(1000):
                try:
                    self.device.write(packet)
                    break
                except Exception:
                    time.sleep(0.001)
            else:
                self.device.write(packet)

    def _send_stp(self):
        """Sends the STP command synchronously to commit changes."""
        self._send_command(bytes([0x53, 0x54, 0x50]))

    def _initialize_device(self):
        if self._initialized:
            return
        self._initialized = True
        self._send_command(bytes([0x44, 0x49, 0x53]))
        self._send_command(bytes([0x4C, 0x49, 0x47, 0x00, 0x00, 0x00, 0x00]))

    def _keepalive_worker(self):
        time.sleep(1.0)
        while self._run_keepalive:
            try:
                self._initialize_device()
                self._send_command(bytes([0x43, 0x4F, 0x4E, 0x4E, 0x45, 0x43, 0x54]))
            except Exception:
                pass
            for _ in range(100):
                if not self._run_keepalive:
                    break
                time.sleep(0.1)

    def _start_keepalive(self):
        self._stop_keepalive()
        self._run_keepalive = True
        self._keepalive_thread = threading.Thread(target=self._keepalive_worker, daemon=True)
        self._keepalive_thread.start()

    def _stop_keepalive(self):
        self._run_keepalive = False
        if self._keepalive_thread is not None:
            try:
                self._keepalive_thread.join(timeout=2.0)
            except RuntimeError:
                pass
            self._keepalive_thread = None

    def open(self, resume_from_suspend=True):
        self.device.open()
        self.reconnect_after_suspend = resume_from_suspend
        self._initialized = False

        if resume_from_suspend:
            self._setup_reader(self._read_with_resume_from_suspend)
        else:
            self._setup_reader(self._read)

        self.set_brightness(100)
        self._clear_all_keys()
        self._start_keepalive()

    def close(self):
        self._stop_keepalive()
        self.run_read_thread = False

        try:
            with self._write_lock:
                self._send_command(bytes([0x43, 0x4C, 0x45, 0x00, 0x00, 0x44, 0x43]))
                self._send_command(bytes([0x48, 0x41, 0x4E]))
        except Exception:
            pass

        self.device.close()

    def _trigger_momentary_key(self, key_index):
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
        total_keys = self.KEY_COUNT + self.TOUCH_KEY_COUNT
        data = self.device.read(512)
        if data is None or len(data) == 0:
            return None
        if len(data) < 11 or data[:3] != self._ACK_PREFIX:
            return {}

        event_code = data[9]
        event_state = data[10]

        if event_code in self._LCD_KEY_HW_MAP:
            states = [False] * total_keys
            key_index = self._LCD_KEY_HW_MAP[event_code]
            states[key_index] = (event_state == 0x01)
            return {ControlType.KEY: states}

        if event_code in self._SECOND_SCREEN_MAP:
            if event_state == 0x00:
                key_index = self._SECOND_SCREEN_MAP[event_code]
                self._trigger_momentary_key(key_index)
                
                if self.touchscreen_callback is not None:
                    from .StreamDeck import TouchscreenEventType
                    dial_index = key_index - self.KEY_COUNT
                    x_coord = (dial_index * 200) + 100
                    try:
                        self.touchscreen_callback(self, TouchscreenEventType.SHORT, {'x': x_coord, 'y': 50})
                    except Exception:
                        pass
            return {}

        if event_code in self._ENCODER_ROTATE_MAP:
            dial_index, amount = self._ENCODER_ROTATE_MAP[event_code]
            values = [0] * self.DIAL_COUNT
            values[dial_index] = amount
            return {ControlType.DIAL: {DialEventType.TURN: values}}

        if event_code in self._ENCODER_PRESS_MAP:
            if event_state == 0x01:
                self._trigger_momentary_dial(self._ENCODER_PRESS_MAP[event_code])
            return {}

        if event_code in self._SWIPE_MAP:
            from .StreamDeck import TouchscreenEventType
            direction = self._SWIPE_MAP[event_code]
            x_in = 0 if direction == "left" else self.SCREEN_PIXEL_WIDTH
            x_out = self.SCREEN_PIXEL_WIDTH if direction == "left" else 0
            return {
                ControlType.TOUCHSCREEN: (
                    TouchscreenEventType.DRAG,
                    {
                        "x": x_in,
                        "y": 0,
                        "x_out": x_out,
                        "y_out": 0,
                        "direction": direction
                    }
                )
            }
        return {}

    def _clear_all_keys(self):
        self._initialize_device()
        self._send_command(bytes([0x43, 0x4C, 0x45, 0x00, 0x00, 0x00, 0xFF]))
        self._send_stp()

    def _reset_key_stream(self):
        self._initialize_device()
        self.set_brightness(100)
        self._clear_all_keys()

    def reset(self):
        self._reset_key_stream()

    def set_brightness(self, percent):
        self._initialize_device()
        if isinstance(percent, float):
            percent = int(100.0 * percent)
        percent = min(max(percent, 0), 100)
        self._send_command(bytes([0x4C, 0x49, 0x47, 0x00, 0x00, percent]))

    def get_serial_number(self):
        return self.device.serial_number()

    def get_firmware_version(self):
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

    def _send_image_data(self, hw_key_id, image_data):
        self._initialize_device()
        image_data = bytes(image_data)
        bat_cmd = bytes([
            0x42, 0x41, 0x54,
            0x00, 0x00,
            (len(image_data) >> 8) & 0xFF,
            len(image_data) & 0xFF,
            hw_key_id,
        ])
        with self._write_lock:
            self._send_command(bat_cmd)
            offset = 0
            while offset < len(image_data):
                chunk = image_data[offset:offset + 1024]
                for _ in range(1000):
                    try:
                        self.device.write(self._make_packet(chunk))
                        break
                    except Exception:
                        time.sleep(0.001)
                else:
                    self.device.write(self._make_packet(chunk))
                offset += 1024
        self._send_stp()

    def set_key_image(self, key, image):
        if key < 0 or key >= (self.KEY_COUNT + self.TOUCH_KEY_COUNT):
            raise IndexError("Invalid key index {}.".format(key))

        if key < self.KEY_COUNT:
            hw_key_id = self._KEY_TO_HW_ID[key]
        else:
            if key not in self._SECOND_SCREEN_TO_HW_ID:
                raise IndexError("Invalid secondary screen button index {}.".format(key))
            hw_key_id = self._SECOND_SCREEN_TO_HW_ID[key]

        if image is None:
            self._initialize_device()
            self._send_command(bytes([0x43, 0x4C, 0x45, 0x00, 0x00, 0x00, hw_key_id]))
            self._send_stp()
            return

        # N4 hardware needs smaller JPEG to avoid memory overflow
        if self.RECOMPRESS_KEY_IMAGES:
            from PIL import Image as PILImage
            from io import BytesIO
            try:
                img = PILImage.open(BytesIO(image))
                with BytesIO() as buf:
                    img.save(buf, 'JPEG', quality=self.JPEG_QUALITY)
                    image = buf.getvalue()
                img.close()
            except Exception:
                pass

        self._send_image_data(hw_key_id, image)

    def set_second_screen_image(self, button_index, image):
        key_index = button_index + self.KEY_COUNT
        if key_index not in self._SECOND_SCREEN_TO_HW_ID:
            raise IndexError("Invalid secondary screen button index {}.".format(button_index))

        hw_key_id = self._SECOND_SCREEN_TO_HW_ID[key_index]
        if image is None:
            self._initialize_device()
            self._send_command(bytes([0x43, 0x4C, 0x45, 0x00, 0x00, 0x00, hw_key_id]))
            self._send_stp()
            return
        self._send_image_data(hw_key_id, image)

    def set_touchscreen_image(self, image, x_pos=0, y_pos=0, width=0, height=0):
        if image is None:
            return
        from PIL import Image as PILImage
        from io import BytesIO
        img = PILImage.open(BytesIO(image))
        section_width = img.width // 4

        for i in range(4):
            left = i * section_width
            
            # The physical button is 176px wide, but the virtual slot is 200px wide.
            # We crop the center 176px of the 200px slot to bypass the physical gaps 
            # and maintain a 1:1 pixel mapping without horizontal squashing.
            crop_margin_x = (section_width - self.TOUCHBAR_KEY_PIXEL_WIDTH) // 2
            section = img.crop((left + crop_margin_x, 0, left + section_width - crop_margin_x, img.height))
            
            # The physical button is 112px high, but the virtual image is 100px high.
            # We paste it centered vertically on a black background to avoid vertical stretching.
            section_bg = PILImage.new("RGB", (self.TOUCHBAR_KEY_PIXEL_WIDTH, self.TOUCHBAR_KEY_PIXEL_HEIGHT), "black")
            paste_y = (self.TOUCHBAR_KEY_PIXEL_HEIGHT - section.height) // 2
            section_bg.paste(section, (0, paste_y))
            
            section_final = section_bg.rotate(self.SECOND_SCREEN_ROTATION)
            
            with BytesIO() as buf:
                section_final.save(buf, 'JPEG', quality=self.JPEG_QUALITY)
                jpeg_data = buf.getvalue()

            key_index = i + self.KEY_COUNT
            hw_key_id = self._SECOND_SCREEN_TO_HW_ID[key_index]
            self._send_image_data(hw_key_id, jpeg_data)
        img.close()

    def set_key_color(self, key, r, g, b):
        pass

    def set_screen_image(self, image):
        pass

    def key_image_format(self):
        return {
            'size': (self.KEY_PIXEL_WIDTH, self.KEY_PIXEL_HEIGHT),
            'format': self.KEY_IMAGE_FORMAT,
            'rotation': self.KEY_ROTATION,
            'flip': self.KEY_FLIP,
        }

    def second_screen_image_format(self):
        return {
            'size': (self.TOUCHBAR_KEY_PIXEL_WIDTH, self.TOUCHBAR_KEY_PIXEL_HEIGHT),
            'format': self.SECOND_SCREEN_IMAGE_FORMAT,
            'rotation': self.SECOND_SCREEN_ROTATION,
            'flip': (False, False),
        }

    def touchscreen_image_format(self):
        return {
            'size': (self.TOUCHBAR_BG_PIXEL_WIDTH, self.TOUCHBAR_KEY_PIXEL_HEIGHT),
            'format': self.SCREEN_IMAGE_FORMAT,
            'rotation': 0,
            'flip': (False, False),
        }
