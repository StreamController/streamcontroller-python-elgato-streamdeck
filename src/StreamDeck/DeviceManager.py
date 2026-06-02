#         Python Stream Deck Library
#      Released under the MIT license
#
#   dean [at] fourwalledcubicle [dot] com
#         www.fourwalledcubicle.com
#

from .Devices.StreamDeck import StreamDeck
from .Devices.StreamDeckMini import StreamDeckMini
from .Devices.StreamDeckNeo import StreamDeckNeo
from .Devices.StreamDeckOriginal import StreamDeckOriginal
from .Devices.StreamDeckOriginalV2 import StreamDeckOriginalV2
from .Devices.StreamDeckXL import StreamDeckXL
from .Devices.StreamDeckPedal import StreamDeckPedal
from .Devices.StreamDeckPlus import StreamDeckPlus
from .Transport import Transport
from .Devices.Mirabox293S import Mirabox293S
from .Devices.MiraboxN3 import MiraboxN3
from .Devices.MiraboxN4 import MiraboxN4
from .Devices.MiraboxN4Pro import MiraboxN4Pro
from .Transport.Dummy import Dummy
from .Transport.LibUSBHIDAPI import LibUSBHIDAPI
from .ProductIDs import USBVendorIDs, USBProductIDs


class ProbeError(Exception):
    """
    Exception thrown when attempting to probe for attached StreamDeck devices,
    but no suitable valid transport was found.
    """

    pass


class DeviceManager:
    """
    Central device manager, to enumerate any attached StreamDeck devices. An
    instance of this class must be created in order to detect and use any
    StreamDeck devices.
    """
    USB_VID_ELGATO = USBVendorIDs.USB_VID_ELGATO
    USB_PID_STREAMDECK_ORIGINAL = USBProductIDs.USB_PID_STREAMDECK_ORIGINAL
    USB_PID_STREAMDECK_ORIGINAL_V2 = USBProductIDs.USB_PID_STREAMDECK_ORIGINAL_V2
    USB_PID_STREAMDECK_MINI = USBProductIDs.USB_PID_STREAMDECK_MINI
    USB_PID_STREAMDECK_XL = USBProductIDs.USB_PID_STREAMDECK_XL
    USB_PID_STREAMDECK_MK2 = USBProductIDs.USB_PID_STREAMDECK_MK2
    USB_PID_STREAMDECK_PEDAL = USBProductIDs.USB_PID_STREAMDECK_PEDAL
    USB_PID_STREAMDECK_PLUS = USBProductIDs.USB_PID_STREAMDECK_PLUS
    USB_PID_STREAMDECK_NEO = USBProductIDs.USB_PID_STREAMDECK_NEO

    @staticmethod
    def _get_transport(transport: str | None):
        """
        Creates a new HID transport instance from the given transport back-end
        name. If no specific transport is supplied, an attempt to find an
        installed backend will be made.

        :param str transport: Name of a supported HID transport back-end to use, None to autoprobe.

        :rtype: Transport.* instance
        :return: Instance of a HID Transport class
        """

        transports = {
            "dummy": Dummy,
            "libusb": LibUSBHIDAPI,
        }

        if transport:
            transport_class = transports.get(transport)

            if transport_class is None:
                raise ProbeError("Unknown HID transport backend \"{}\".".format(transport))

            try:
                transport_class.probe()
                return transport_class()
            except Exception as transport_error:
                raise ProbeError("Probe failed on HID backend \"{}\".".format(transport), transport_error)
        else:
            probe_errors = {}

            for transport_name, transport_class in transports.items():
                if transport_name == "dummy":
                    continue

                try:
                    transport_class.probe()
                    return transport_class()
                except Exception as transport_error:
                    probe_errors[transport_name] = transport_error

            raise ProbeError("Probe failed to find any functional HID backend.", probe_errors)

    def __init__(self, transport: str | None = None):
        """
        Creates a new StreamDeck DeviceManager, used to detect attached StreamDeck devices.

        :param str transport: name of the the specific HID transport back-end to use, None to auto-probe.
        """
        self.transport: Transport.Transport = self._get_transport(transport)

    def enumerate(self) -> list[StreamDeck]:
        """
        Detect attached StreamDeck devices.

        :rtype: list(StreamDeck)
        :return: list of :class:`StreamDeck` instances, one for each detected device.
        """

        # Each entry is (VID, PID, DeviceClass) or (VID, PID, DeviceClass, usage_page).
        # When usage_page is specified, only HID interfaces matching that usage_page
        # are returned. This is critical for multi-interface devices like the Mirabox N3
        # which expose both a vendor-specific command endpoint (0xFFA0) and a
        # generic HID keyboard endpoint.
        products = [
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_ORIGINAL, StreamDeckOriginal),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_ORIGINAL_V2, StreamDeckOriginalV2),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_MK2_SCISSOR, StreamDeckOriginalV2),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_MK2_MODULE, StreamDeckOriginalV2),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_MINI, StreamDeckMini),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_NEO, StreamDeckNeo),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_XL, StreamDeckXL),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_MK2, StreamDeckOriginalV2),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_MK2_V2, StreamDeckOriginalV2),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_PEDAL, StreamDeckPedal),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_MINI_MK2, StreamDeckMini),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_MINI_MK2_MODULE, StreamDeckMini),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_XL_V2, StreamDeckXL),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_XL_V2_MODULE, StreamDeckXL),
            (USBVendorIDs.USB_VID_ELGATO, USBProductIDs.USB_PID_STREAMDECK_PLUS, StreamDeckPlus),
            (USBVendorIDs.USB_VID_MIRABOX, USBProductIDs.USB_PID_MIRABOX_STREAMDOCK_293S, Mirabox293S),
            # AJAZZ AKP03 / Mirabox N3 — usage_page 0xFFA0 selects the vendor command interface
            (USBVendorIDs.USB_VID_AJAZZ, USBProductIDs.USB_PID_AJAZZ_AKP03, MiraboxN3, 0xFFA0),
            (USBVendorIDs.USB_VID_AJAZZ, USBProductIDs.USB_PID_AJAZZ_AKP03E, MiraboxN3, 0xFFA0),
            (USBVendorIDs.USB_VID_AJAZZ, USBProductIDs.USB_PID_AJAZZ_AKP03R, MiraboxN3, 0xFFA0),
            (USBVendorIDs.USB_VID_AJAZZ, USBProductIDs.USB_PID_AJAZZ_AKP03R_V2, MiraboxN3, 0xFFA0),
            (USBVendorIDs.USB_VID_MIRABOX_N3_V2, USBProductIDs.USB_PID_MIRABOX_N3_V2, MiraboxN3, 0xFFA0),
            (USBVendorIDs.USB_VID_MIRABOX_N3_V2, USBProductIDs.USB_PID_MIRABOX_N3_V2E, MiraboxN3, 0xFFA0),
            (USBVendorIDs.USB_VID_MIRABOX_N3_V2, USBProductIDs.USB_PID_MIRABOX_N3_V2E_OLD, MiraboxN3, 0xFFA0),
            (USBVendorIDs.USB_VID_MIRABOX_N3_V25, USBProductIDs.USB_PID_MIRABOX_N3_V25, MiraboxN3, 0xFFA0),
            (USBVendorIDs.USB_VID_MIRABOX_N3_V25, USBProductIDs.USB_PID_MIRABOX_N3_V25E, MiraboxN3, 0xFFA0),
            # Mirabox N4 / AJAZZ AKP05 — usage_page 0xFFA0 selects the vendor command interface
            (USBVendorIDs.USB_VID_MIRABOX_N3_V2, USBProductIDs.USB_PID_MIRABOX_N4, MiraboxN4, 0xFFA0),
            (USBVendorIDs.USB_VID_MIRABOX_N3_V25, USBProductIDs.USB_PID_MIRABOX_N4E, MiraboxN4, 0xFFA0),
            (USBVendorIDs.USB_VID_AJAZZ, USBProductIDs.USB_PID_AJAZZ_AKP05E, MiraboxN4, 0xFFA0),
            (USBVendorIDs.USB_VID_AJAZZ, USBProductIDs.USB_PID_AJAZZ_AKP05_PROVISIONAL, MiraboxN4, 0xFFA0),
            # Mirabox N4 Pro — VID 0x5548
            (USBVendorIDs.USB_VID_MIRABOX, USBProductIDs.USB_PID_MIRABOX_N4_PRO, MiraboxN4Pro, 0xFFA0),
            (USBVendorIDs.USB_VID_MIRABOX, USBProductIDs.USB_PID_MIRABOX_N4_PRO_E, MiraboxN4Pro, 0xFFA0),
        ]

        streamdecks = list()

        for product_entry in products:
            vid = product_entry[0]
            pid = product_entry[1]
            class_type = product_entry[2]
            required_usage_page = product_entry[3] if len(product_entry) > 3 else None

            found_devices = self.transport.enumerate(vid=vid, pid=pid)

            if required_usage_page is not None:
                found_devices = [d for d in found_devices
                                 if d.device_info.get('usage_page') == required_usage_page
                                 or (d.device_info.get('usage_page') in (0, None) and d.device_info.get('interface_number') == 0)]

            streamdecks.extend([class_type(d) for d in found_devices])

        return streamdecks

