#         Python Stream Deck Library
#      Released under the MIT license
#
#   dean [at] fourwalledcubicle [dot] com
#         www.fourwalledcubicle.com
#

from collections.abc import Callable

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
from .Devices.AjazzAKP03E import AjazzAKP03E, SoomfonCN002
from .Transport.Dummy import Dummy
from .Transport.LibUSBHIDAPI import LibUSBHIDAPI
from .ProductIDs import USBVendorIDs, USBProductIDs


# Module-level registry of additional controller factories. These are intended
# for non-USB / virtual controllers (e.g. the TouchyDeck shim) that cannot be
# discovered via the standard USB transport. The built-in Elgato and Mirabox
# devices are always enumerated regardless of this registry.
_controller_factories: list[Callable[[], "list[StreamDeck]"]] = []


def register_controllers_factory(factory: Callable[[], "list[StreamDeck]"]) -> None:
    """
    Register an additional controller factory whose devices are included in
    every :meth:`DeviceManager.enumerate` call.

    A factory is a zero-argument callable that returns a list of
    :class:`StreamDeck` (or subclass) instances. Factories are intended for
    non-USB / virtual controllers that can't be discovered via the standard
    USB transport; the built-in Elgato and Mirabox devices are always
    enumerated regardless.

    Registration is global and cumulative. It is typically called once at
    application startup, before any :class:`DeviceManager` is instantiated.

    :param Callable factory: callable returning ``list[StreamDeck]``.
    """
    _controller_factories.append(factory)


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

    def _default_factory(self) -> list[StreamDeck]:
        """
        Discover the built-in, USB-attached StreamDeck controllers via this
        manager's transport. Returns the Elgato and Mirabox devices that the
        library knows about natively.

        :rtype: list(StreamDeck)
        :return: list of :class:`StreamDeck` instances, one for each detected
                 USB device.
        """

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
            (USBVendorIDs.USB_VID_AJAZZ, USBProductIDs.USB_PID_AJAZZ_AKP03E, AjazzAKP03E),
            (USBVendorIDs.USB_VID_SOOMFON, USBProductIDs.USB_PID_SOOMFON_CN002, SoomfonCN002)
        ]

        streamdecks = list()

        for vid, pid, class_type in products:
            found_devices = self.transport.enumerate(vid=vid, pid=pid)

            # This device has a second HID interface as a keyboard
            if getattr(class_type, "IGNORE_SECOND_HID_DEVICE", False):
                found_devices = found_devices[::2]
            streamdecks.extend([class_type(d) for d in found_devices])

        return streamdecks

    def enumerate(self) -> list[StreamDeck]:
        """
        Detect attached StreamDeck devices, including any registered via
        :func:`register_controllers_factory`.

        Combines the built-in USB-attached devices discovered by
        :meth:`_default_factory` with the devices returned by every
        registered controller factory.

        :rtype: list(StreamDeck)
        :return: list of :class:`StreamDeck` instances, one for each detected
                 device, from both the built-in USB transport and every
                 registered controller factory.
        """

        streamdecks = list()
        streamdecks.extend(self._default_factory())
        for factory in _controller_factories:
            streamdecks.extend(factory())

        return streamdecks
