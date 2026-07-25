#         Python Stream Deck Library
#      Released under the MIT license
#
#   dean [at] fourwalledcubicle [dot] com
#         www.fourwalledcubicle.com
#

from .StreamDeckPlus import StreamDeckPlus


class StreamDeckPlusXL(StreamDeckPlus):
    KEY_COUNT = 36
    KEY_COLS = 9
    KEY_ROWS = 4

    DIAL_COUNT = 6

    KEY_PIXEL_WIDTH = 120
    KEY_PIXEL_HEIGHT = 120
    KEY_IMAGE_FORMAT = "JPEG"
    KEY_FLIP = (False, False)
    KEY_ROTATION = 0

    DECK_TYPE = "Stream Deck + XL"
    DECK_VISUAL = True
    DECK_TOUCH = True

    TOUCHSCREEN_PIXEL_HEIGHT = 100
    TOUCHSCREEN_PIXEL_WIDTH = 1000
    TOUCHSCREEN_IMAGE_FORMAT = "JPEG"
    TOUCHSCREEN_FLIP = (False, False)
    TOUCHSCREEN_ROTATION = 0
