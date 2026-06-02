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
