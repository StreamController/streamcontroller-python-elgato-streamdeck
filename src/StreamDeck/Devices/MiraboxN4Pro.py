#         Python Stream Deck Library
#      Released under the MIT license
#
#   Mirabox StreamDock N4 Pro adapter
#   Extends the N4 protocol to add BGPIC background rendering and RGB LEDs.

import struct
import time
from .MiraboxN4 import MiraboxN4
from .StreamDeck import ControlType, DialEventType

class MiraboxN4Pro(MiraboxN4):
    """
    Represents a physically attached Mirabox StreamDock N4 Pro device.
    Provides native support for the N4 Pro's full-screen background rendering (BGPIC)
    and custom RGB dial LEDs.
    """

    TOUCHBAR_PIXEL_HEIGHT = 112
    JPEG_QUALITY = 100
    RECOMPRESS_KEY_IMAGES = False

    def __init__(self, device):
        super().__init__(device)
        
        # N4 Pro RGB LED state
        self._led_colors = [(255, 255, 255)] * 4
        self._led_brightness = [100] * 4
        self._global_led_brightness = 100

    def deck_type(self):
        return "Mirabox StreamDock N4 Pro"

    def key_image_format(self):
        # Allow base class to handle 180 rotation, as physical screen is inverted
        # but StreamController core expects to handle the rotation natively.
        return super().key_image_format()

    def has_background_image(self) -> bool:
        return True

    def background_image_format(self):
        return {
            'size': (800, 480),
            'format': 'JPEG',
            'rotation': 180,
            'flip': (False, False),
        }

    def _send_bgpic_data(self, image_data, x=0, y=0, width=800, height=480, layer=0x00):
        self._initialize_device()
        image_data = bytes(image_data)

        # BGPIC command structure: 
        # BGPIC (5 bytes)
        # length (4 bytes Big-Endian)
        # x_hi, x_lo
        # y_hi, y_lo
        # w_hi, w_lo
        # h_hi, h_lo
        # layer
        bgpic_cmd = bytearray([0x42, 0x47, 0x50, 0x49, 0x43])
        bgpic_cmd.extend(struct.pack('>I', len(image_data)))
        bgpic_cmd.extend(struct.pack('>HHHHB', x, y, width, height, layer))
        
        with self._write_lock:
            # Note: the C++ wrapper prepends CRT 0x00 0x00 for the CRT command automatically in its lower level send
            # wait, in MiraboxN4 _send_command() prepends CRT 0x00 0x00 to whatever is sent.
            self._send_command(bytes(bgpic_cmd))
            
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

    def set_background_image(self, image_data, x=0, y=0, width=800, height=480, layer=0x00):
        if image_data is None:
            # Not natively supported to just send None, clear the layer instead
            self.clear_background_image(layer)
            return

        # N4 Pro expects JPEG data for background
        self._send_bgpic_data(image_data, x, y, width, height, layer)

    def clear_background_image(self, layer=0x03):
        # 0x03 clears all layers according to SDK
        self._initialize_device()
        
        # Command is likely BGCLE followed by the layer byte
        bgcle_cmd = bytearray([0x42, 0x47, 0x43, 0x4C, 0x45, layer])
        
        with self._write_lock:
            self._send_command(bytes(bgcle_cmd))
        self._send_stp()


    def set_led_brightness(self, percent):
        """Sets the brightness of the RGB LEDs (0-100)."""
        self._initialize_device()
        if isinstance(percent, float):
            percent = int(100.0 * percent)
        percent = min(max(percent, 0), 100)
        self._global_led_brightness = percent

        hw_percent = max(1, percent)
        self._send_command(bytes([0x4C, 0x42, 0x4C, 0x49, 0x47, hw_percent]))
        self._update_hw_led_colors()

    def set_led_colors(self, colors):
        """Sets the base colors of the individual RGB LEDs (up to 4)."""
        for i, color in enumerate(colors[:4]):
            self._led_colors[i] = color
        self._update_hw_led_colors()
 
    def set_individual_led_brightness(self, index, percent):
        """Sets the brightness of a specific LED by scaling its RGB output (0-100)."""
        if 0 <= index < 4:
            self._led_brightness[index] = min(max(int(percent), 0), 100)
            self._update_hw_led_colors()

    def set_led_color(self, r, g, b):
        """Sets all RGB LEDs to the same color."""
        self.set_led_colors([(r, g, b)] * 4)

    def reset_led_color(self):
        """Resets the RGB LEDs to their default color/state."""
        self._initialize_device()
        self._send_command(b"DELED")

    def _update_hw_led_colors(self):
        """Applies individual and global brightness to the base colors and sends SETLB."""
        self._initialize_device()
        cmd = bytearray(b"SETLB")
        for i in range(4):
            r, g, b = self._led_colors[i]
            if self._global_led_brightness == 0:
                scale = 0.0
            else:
                scale = self._led_brightness[i] / 100.0
            cmd.extend([int(r * scale), int(g * scale), int(b * scale)])

        if all(x == 0 for x in cmd[5:]):
            cmd[-1] = 1

        with self._write_lock:
            self._send_command(cmd)
