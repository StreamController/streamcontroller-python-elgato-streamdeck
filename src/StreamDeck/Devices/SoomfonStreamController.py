#         Python Stream Deck Library
#      Released under the MIT license
#
#   dean [at] fourwalledcubicle [dot] com
#         www.fourwalledcubicle.com
#

import io
import threading

from .Mirabox293S import Mirabox293S
from .StreamDeck import ControlType
from ..Transport.Transport import TransportError


class SoomfonStreamController(Mirabox293S):
    """
    Represents a physically attached Soomfon Stream Controller (model
    XF-CN001, USB 1500:3003), a rebranded Mirabox 293S variant that speaks a
    newer revision of the Mirabox protocol ("v3" in the mirajazz project).

    Differences from :class:`Mirabox293S`, verified on hardware:

    - output reports are 1024 bytes (plus report ID) instead of 512;
    - key images are 95x95 JPEG; the three side-display tiles (keys 5, 11 and
      17) are 82x82 and are resized internally, so callers can keep using a
      single key image format;
    - the device reports distinct press and release events (byte 9 = key ID,
      byte 10 = state), so no press/release emulation is needed;
    - a ``CONNECT`` heartbeat is sent periodically while the deck is open.

    The deck exposes two HID interfaces under the same VID/PID; only
    interface 0 speaks the deck protocol (interface 1 is a keyboard), see
    the interface filter in :class:`DeviceManager`.
    """

    DECK_TYPE = "Soomfon Stream Controller"

    PACKET_LENGHT = 1024
    INPUT_REPORT_LENGTH = 512

    KEY_PIXEL_WIDTH = 95
    KEY_PIXEL_HEIGHT = 95

    SIDE_KEYS = (5, 11, 17)
    SIDE_KEY_PIXEL_SIZE = 82

    HEARTBEAT_INTERVAL = 8.0

    _CMD_PREFIX = [0x43, 0x52, 0x54, 0x00, 0x00]  # "CRT\0\0"
    _CMD_CONNECT = _CMD_PREFIX + [0x43, 0x4F, 0x4E, 0x4E, 0x45, 0x43, 0x54]  # "CONNECT"
    _CMD_STP = _CMD_PREFIX + [0x53, 0x54, 0x50]  # "STP"

    def __init__(self, device):
        super().__init__(device)
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None

    def _make_payload_for_report_id(self, report_id, payload_data):
        payload = bytearray(self.PACKET_LENGHT + 1)
        payload[0] = report_id
        payload[1:1 + len(payload_data)] = payload_data
        return payload

    def _send_command(self, command):
        self.device.write(self._make_payload_for_report_id(0x00, command))

    def _read_control_states(self):
        data = self.device.read(self.INPUT_REPORT_LENGTH)
        if not data or len(data) < 11 or bytes(data[:3]) != b"ACK":
            return None

        key_id, state = data[9], data[10]
        if key_id not in self.KEY_DEVICE_KEY_ID_TO_NUM:
            return None

        states = list(self.last_key_states)
        states[self._convert_device_key_id_to_key_num(key_id)] = bool(state)
        return {ControlType.KEY: states}

    def reset(self):
        with self.update_lock:
            super().reset()
            self._send_command(self._CMD_STP)

    def _blank_image(self, size):
        from PIL import Image

        with io.BytesIO() as buffer:
            Image.new("RGB", (size, size), "black").save(buffer, "JPEG", quality=90)
            return buffer.getvalue()

    def _fit_image(self, image, size):
        from PIL import Image

        decoded = Image.open(io.BytesIO(image))
        if decoded.size == (size, size):
            return image

        with io.BytesIO() as buffer:
            decoded.convert("RGB").resize((size, size), Image.LANCZOS).save(buffer, "JPEG", quality=90)
            return buffer.getvalue()

    def set_key_image(self, key, image):
        if min(max(key, 0), self.KEY_COUNT - 1) != key:
            raise IndexError("Invalid key index {}.".format(key))

        size = self.SIDE_KEY_PIXEL_SIZE if key in self.SIDE_KEYS else self.KEY_PIXEL_WIDTH
        image = self._fit_image(bytes(image), size) if image else self._blank_image(size)

        # Keep the BAT/data/STP sequence atomic with respect to the heartbeat.
        with self.update_lock:
            super().set_key_image(key, image)

    def _heartbeat_worker(self):
        while not self._heartbeat_stop.wait(self.HEARTBEAT_INTERVAL):
            try:
                with self.update_lock:
                    self._send_command(self._CMD_CONNECT)
            except TransportError:
                # Device temporarily unavailable (e.g. host suspend); the read
                # thread handles reconnection, keep trying until close().
                pass

    def open(self, resume_from_suspend: bool = True) -> None:
        super().open(resume_from_suspend)
        self._heartbeat_stop.clear()
        if self._heartbeat_thread is None or not self._heartbeat_thread.is_alive():
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_worker, name="SoomfonHeartbeat", daemon=True
            )
            self._heartbeat_thread.start()

    def close(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None and self._heartbeat_thread is not threading.current_thread():
            self._heartbeat_thread.join(timeout=self.HEARTBEAT_INTERVAL)
        self._heartbeat_thread = None
        super().close()
