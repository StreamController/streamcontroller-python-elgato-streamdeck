#         Python Stream Deck Library
#      Released under the MIT license
#
#   dean [at] fourwalledcubicle [dot] com
#         www.fourwalledcubicle.com
#
#   Ulanzi Stream Controller D200 support
#   Protocol reverse-engineered by redphx (github.com/redphx/strmdck,
#   github.com/redphx/homedeck)

from __future__ import annotations

import io
import json
import logging
import queue
import threading
import time
import zipfile
from datetime import datetime

from .StreamDeck import ControlType, StreamDeck
from ..Transport.Transport import TransportError


class UlanziD200(StreamDeck):
    """
    Represents a physically attached Ulanzi Stream Controller D200 device.

    Protocol notes (USB VID:PID 2207:0019). None of this is documented by
    the vendor; all of it was learned empirically against real hardware.

    - The device exposes two HID interfaces. Interface 0 carries the deck
      protocol used here; interface 1 is a keyboard-emulation HID device
      (unrelated, not touched by this class). DeviceManager filters for
      interface 0 specifically when discovering this device - see the
      ``interface_number`` handling in ``DeviceManager._default_factory``.
    - 14 physical keys, laid out row-major in a 5 col x 3 row grid (index //
      5 = row, index % 5 = col). The 15th grid slot (row 2, col 4) has no
      physical LCD - it's occupied by a small stats/clock window. StreamDeck
      key layout math generally assumes a fully rectangular grid, so
      KEY_COUNT is 15 with that slot treated as an inert phantom key that
      set_key_image() silently ignores.
    - The device stops sending button-press reports unless it receives a
      periodic "keep alive" packet (a small-window-data update); without it
      the device falls back to standalone/disconnected behavior.
    - The device only accepts a button image upload for a short window after
      the connection opens. If nothing arrives within roughly a few seconds
      it silently ignores every future upload for the rest of the session.
      This class works around that by periodically sending a harmless
      "priming" upload (to the invisible phantom slot) until the caller's
      first real upload arrives, keeping the window open indefinitely.
    - Button image uploads for multiple keys are combined into a single
      multi-packet upload rather than one upload per key (matching how
      other Ulanzi D200 clients such as homedeck operate); the device merges
      an upload into the currently displayed buttons rather than replacing
      them, so a partial-page upload never clears keys it doesn't mention.
    - Icons appear to be cached by filename on the device, not by content:
      re-uploading a key's icon under the same "<key>.png" name can leave
      the old image on screen even though the upload was accepted without
      error. Each update therefore uses a fresh, never-reused filename
      ("<key>_<revision>.png").

    Threading model: a single background "send worker" thread owns every
    write to the device - the periodic keep-alive/priming pings as well as
    button image uploads. Earlier iterations sent the keep-alive from the
    StreamDeck base class's own reader thread while a separate thread
    handled image uploads; even though every individual write was already
    byte-correct and serialized under a lock, that interleaving alone was
    enough to make the device silently stop rendering uploaded images.
    Funneling everything through one thread and one queue avoided that
    entirely. The worker is started in open() and stopped in close(), so it
    doesn't leak across a close()/open() reconnect cycle on the same
    instance (StreamDeck._read_with_resume_from_suspend() reuses the same
    object across a disconnect/reconnect rather than constructing a new
    one).
    """

    KEY_COUNT = 15
    PHYSICAL_KEY_COUNT = 14
    PHANTOM_KEY = 14
    KEY_COLS = 5
    KEY_ROWS = 3

    KEY_PIXEL_WIDTH = 196
    KEY_PIXEL_HEIGHT = 196
    KEY_IMAGE_FORMAT = "PNG"
    KEY_FLIP = (False, False)
    KEY_ROTATION = 0

    DECK_TYPE = "Ulanzi Stream Controller D200"
    DECK_VISUAL = True
    DECK_TOUCH = False

    # Protocol command IDs, sent as the second field of every packet header
    # (see _build_packet()). Only the commands this class actually needs are
    # listed; the device almost certainly supports more (label styling,
    # partial-button updates, etc.) that weren't required to get a working
    # driver and so weren't reverse-engineered.
    _OUT_SET_BUTTONS = 0x0001
    _OUT_SET_SMALL_WINDOW_DATA = 0x0006
    _OUT_SET_BRIGHTNESS = 0x000A

    _IN_BUTTON = 0x0101

    _CHUNK_SIZE = 1024
    _HEADER_SIZE = 8
    _FIRST_PACKET_PAYLOAD = _CHUNK_SIZE - _HEADER_SIZE  # 1016

    _KEEPALIVE_INTERVAL = 1.0
    _PRIMING_INTERVAL = 1.0
    _FLUSH_DEBOUNCE_SECONDS = 0.3
    _SEND_POLL_SECONDS = 0.2

    # Sentinel command values (no real protocol command is negative), used
    # to ask the send worker to do something other than write a plain
    # packet. See the threading model note in the class docstring for why
    # everything is funneled through this one queue.
    _FORCE_KEEPALIVE = -1
    _FLUSH_BUTTONS = -2

    # Shared across all instances on purpose: it's a fixed, content-free
    # payload (see _priming_zip()), so there's nothing instance-specific to
    # cache separately per device.
    _priming_zip_cache: bytes | None = None

    def __init__(self, device):
        super().__init__(device)

        self._current_key_states = [False] * self.KEY_COUNT
        self._opened_once = False

        self._manifest_lock = threading.Lock()
        self._manifest: dict[str, dict] = {}
        self._icons: dict[str, bytes] = {}
        self._icon_revision: dict[int, int] = {}
        self._flush_timer: threading.Timer | None = None

        self._last_keepalive = 0.0
        self._last_priming = 0.0
        self._send_queue: "queue.Queue[tuple[int, bytes]]" = queue.Queue()
        self._send_stop = threading.Event()
        self._send_thread: threading.Thread | None = None

    # -- send worker ----------------------------------------------------------

    def _start_send_worker(self) -> None:
        """(Re)start the background send worker thread. Called from open();
        safe to call again on a reconnect since it always creates a fresh
        Thread object rather than assuming the old one is still usable."""
        # Reset the timers here rather than in __init__: this can run again
        # on a reconnect (see close()/open()), and stale timestamps from a
        # previous connection would make the very first loop iteration think
        # a keep-alive/priming send is already overdue.
        self._last_keepalive = time.monotonic()
        self._last_priming = time.monotonic()
        self._send_stop.clear()
        self._send_thread = threading.Thread(
            target=self._send_worker, name="UlanziD200SendWorker", daemon=True
        )
        self._send_thread.start()

    def _send_worker(self) -> None:
        """Body of the background send thread (see _start_send_worker() and
        the threading model note in the class docstring). Drains
        self._send_queue, dispatching sentinel commands to their handlers
        and everything else to _write_now(); falls back to the periodic
        keep-alive/priming checks whenever the queue is empty. Runs until
        self._send_stop is set by close()."""
        while not self._send_stop.is_set():
            try:
                command, data = self._send_queue.get(timeout=self._SEND_POLL_SECONDS)
            except queue.Empty:
                command, data = None, None

            try:
                if command == self._FORCE_KEEPALIVE:
                    self._write_keepalive_now()
                    continue

                if command == self._FLUSH_BUTTONS:
                    self._flush_buttons_now()
                    continue

                if command is not None:
                    self._write_now(command, data)
                    continue

                now = time.monotonic()
                if now - self._last_keepalive >= self._KEEPALIVE_INTERVAL:
                    self._write_keepalive_now()
                if now - self._last_priming >= self._PRIMING_INTERVAL:
                    self._write_priming_now()
            except Exception:
                # A single bad iteration must never take the whole worker -
                # and with it, every future write to the device - down.
                logging.exception("UlanziD200: send worker iteration failed")
                continue

    # -- low level packet helpers --------------------------------------------

    @classmethod
    def _build_packet(cls, command: int, length: int, first_chunk: bytes) -> bytes:
        """Build the first (and possibly only) packet of an upload: an
        8-byte header - magic bytes, command ID, total payload length -
        followed by up to _FIRST_PACKET_PAYLOAD bytes of data, zero-padded
        to fill the packet."""
        header = b"\x7c\x7c" + command.to_bytes(2, "big") + length.to_bytes(4, "little")
        return header + first_chunk.ljust(cls._FIRST_PACKET_PAYLOAD, b"\x00")[:cls._FIRST_PACKET_PAYLOAD]

    @classmethod
    def _build_packets(cls, command: int, data: bytes) -> list[bytes]:
        """Split `data` into the full sequence of 1024-byte packets the
        device expects: one header packet (see _build_packet()) followed by
        as many raw, zero-padded continuation packets as needed."""
        packets = [cls._build_packet(command, len(data), data[:cls._FIRST_PACKET_PAYLOAD])]
        for i in range(cls._FIRST_PACKET_PAYLOAD, len(data), cls._CHUNK_SIZE):
            chunk = data[i:i + cls._CHUNK_SIZE]
            packets.append(chunk.ljust(cls._CHUNK_SIZE, b"\x00"))
        return packets

    def _write_now(self, command: int, data: bytes) -> None:
        """Frame `data` into packets and write them to the device
        synchronously. Only ever called from the send worker thread - never
        call this directly from set_key_image()/set_brightness()/etc.,
        which must go through _enqueue() instead so every write stays
        serialized on that one thread."""
        try:
            packets = self._build_packets(command, data)
            with self.update_lock:
                for packet in packets:
                    self.device.write(packet)
        except TransportError:
            # Expected during a disconnect/reconnect cycle; the caller
            # doesn't need every transient write failure logged as an
            # error, only unexpected bugs (handled by the worker's own
            # except Exception, above).
            logging.debug("UlanziD200: write failed (device likely disconnected)", exc_info=True)

    def _write_keepalive_now(self) -> None:
        """Send one keep-alive packet immediately and reset the periodic
        timer. Always called from the send worker thread - see the
        threading model note in the class docstring."""
        self._last_keepalive = time.monotonic()
        # Small-window-data payload format is "<mode>|<cpu>|<mem>|<time>|<gpu>".
        # Mode 2 ("background") rather than 1 ("clock"): the small window
        # still needs *some* periodic update to keep button-press reports
        # flowing, but there's no reason to render a clock over it by
        # default. cpu/mem/gpu are left at 0 since this class doesn't read
        # system stats; `time` is filled in anyway since it's a required
        # positional field, even though it isn't rendered in this mode.
        clock = datetime.now().strftime("%H:%M:%S")
        data = f"2|0|0|{clock}|0".encode("utf-8")
        self._write_now(self._OUT_SET_SMALL_WINDOW_DATA, data)

    def _write_priming_now(self) -> None:
        """Send one priming upload immediately and reset the periodic timer.
        See _priming_zip() and the class docstring for what this works
        around."""
        self._last_priming = time.monotonic()
        self._write_now(self._OUT_SET_BUTTONS, self._priming_zip())

    def _flush_buttons_now(self) -> None:
        """Upload the current contents of self._manifest/self._icons as one
        combined multi-key upload. Called by the send worker in response to
        a debounced set_key_image() burst (see _schedule_flush())."""
        with self._manifest_lock:
            self._flush_timer = None
            manifest_snapshot = dict(self._manifest)
            icons_snapshot = dict(self._icons)

        zip_data = self._build_zip(manifest_snapshot, icons_snapshot)
        self._last_priming = time.monotonic()
        self._write_now(self._OUT_SET_BUTTONS, zip_data)

    @classmethod
    def _priming_zip(cls) -> bytes:
        """A minimal, valid, single-packet OUT_SET_BUTTONS payload uploaded
        to the phantom slot (see PHANTOM_KEY) purely to keep the device's
        upload window open - see the class docstring. It's cached at the
        class level since its content never changes."""
        if cls._priming_zip_cache is None:
            row, col = divmod(cls.PHANTOM_KEY, cls.KEY_COLS)
            slot = f"{col}_{row}"
            manifest = {slot: {"State": 0, "ViewParam": [{"Icon": "icons/p.png"}]}}
            # A pre-built, valid 8x8 black PNG, embedded as raw bytes to
            # avoid a hard dependency on Pillow just for this one fixed
            # payload (generated once with Pillow and frozen here).
            pixel_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x08\x00\x00\x00\x08"
                b"\x08\x02\x00\x00\x00Km)\xdc\x00\x00\x00\x0cIDATx\x9cc`\x18\x1e\x00"
                b"\x00\x00\xc8\x00\x01\xad@v\"\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            cls._priming_zip_cache = cls._build_zip(manifest, {"p.png": pixel_png})
        return cls._priming_zip_cache

    def _enqueue(self, command: int, data: bytes) -> None:
        """Queue a plain (command, data) packet for the send worker to write
        - the normal path for anything that isn't a sentinel command (see
        set_brightness() for an example caller)."""
        self._send_queue.put((command, data))

    def _send_keepalive(self, force: bool = False) -> None:
        """Hook for the base class's _reset_key_stream()/reset(). Ignores
        everything except the forced initial ping - see class docstring."""
        if not force:
            return
        # Only used for the initial "are you there" ping on open/reset; the
        # ongoing periodic keep-alive is entirely owned by _send_worker, so
        # this just enqueues rather than writing directly - see the
        # threading model note in the class docstring for why that matters.
        self._send_queue.put((self._FORCE_KEEPALIVE, b""))

    @staticmethod
    def _build_zip(manifest: dict, icons: dict) -> bytes:
        """Build the zip payload uploaded with OUT_SET_BUTTONS: a
        manifest.json describing which grid slot(s) to update, plus the PNG
        bytes for each referenced icon under icons/.

        The device corrupts the upload if the byte at zip offset 1016,
        1016 + 1024, 1016 + 2048, ... (i.e. right before each 1024-byte
        packet boundary once framed by _build_packets()) is 0x00 or 0x7c -
        both collide with the packet framing/padding on the wire. This
        retries with a growing padding entry until every such offset lands
        on a safe byte.

        The padding entry is written *first*, not appended after the
        manifest/icons: growing a trailing entry only ever shifts bytes
        that come after it, so a bad byte landing inside the manifest or an
        icon (likely for any multi-key page bigger than roughly 1KB) could
        never be fixed that way, and the loop would never terminate.
        Padding first shifts everything after it, including icon data, so
        any bad byte can actually be dodged. The 2000-attempt cap is a
        last-resort safety net; in practice this converges within a handful
        of tries.
        """
        dummy_extra = 0
        data = b""
        for attempt in range(2000):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
                if dummy_extra:
                    zf.writestr("_pad.txt", "x" * dummy_extra)
                zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":"), indent=2))
                for name, icon_data in icons.items():
                    zf.writestr(f"icons/{name}", icon_data)
            data = buf.getvalue()

            if all(data[i:i + 1] not in (b"\x00", b"\x7c") for i in range(1016, len(data), 1024)):
                return data

            dummy_extra += 1

        logging.warning(
            "UlanziD200: could not find zip padding that avoids the framing-byte "
            "corruption bug after %d attempts; uploading as-is (attempt=%d)",
            attempt + 1, attempt,
        )
        return data

    # -- StreamDeck ABC -------------------------------------------------------

    def open(self, resume_from_suspend: bool = True) -> None:
        # StreamController - the primary consumer of this library - calls
        # .open() on a deck up to three times in a row during its own
        # startup, with no is_open() guard between callers. The base
        # implementation unconditionally restarts the reader thread and
        # resends the forced keep-alive on every call; doing that two or
        # three times within milliseconds of each other reliably left this
        # device unable to render any button image for the rest of the
        # session. Make the whole thing idempotent per physical connection;
        # close() resets the flag so a genuine reconnect still gets a fresh
        # open.
        if self._opened_once:
            return

        # Only latch the guard, and only (re)start the send worker, *after*
        # a successful open - if super().open() raises (e.g. the device was
        # briefly claimed by something else), leave _opened_once False so a
        # legitimate retry (including StreamController's own
        # reconnect-after-suspend logic) can still actually open the device
        # instead of silently no-op'ing forever.
        super().open(resume_from_suspend)
        self._opened_once = True
        if self._send_thread is None or not self._send_thread.is_alive():
            self._start_send_worker()

    def _reset_key_stream(self) -> None:
        self._send_keepalive(force=True)

    def close(self) -> None:
        self._opened_once = False
        self._send_stop.set()
        with self._manifest_lock:
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
        super().close()

    def reset(self) -> None:
        with self._manifest_lock:
            self._manifest = {}
            self._icons = {}
            self._icon_revision = {}
        self._send_keepalive(force=True)

    def set_brightness(self, percent) -> None:
        if isinstance(percent, float):
            percent = int(round(percent * 100))
        percent = min(max(int(percent), 0), 100)
        self._enqueue(self._OUT_SET_BRIGHTNESS, str(percent).encode("utf-8"))

    def get_serial_number(self) -> str:
        try:
            return self.device.serial_number() or ""
        except Exception:
            return ""

    def get_firmware_version(self) -> str:
        # Not implemented: no command to query this was identified while
        # reverse-engineering the protocol.
        return ""

    def set_key_color(self, key: int, r: int, g: int, b: int) -> None:
        # The D200's keys are LCDs, not RGB-backlit buttons - there's no
        # protocol command for a flat key color, only full images.
        pass

    def set_touchscreen_image(self, image, x_pos: int = 0, y_pos: int = 0, width: int = 0, height: int = 0) -> None:
        # No touchscreen strip on this device (unlike e.g. the Stream Deck+).
        pass

    def set_screen_image(self, image) -> None:
        # No single full-panel screen on this device - only the per-key LCDs
        # (set_key_image) and the small stats/clock window, which isn't
        # exposed through the base StreamDeck API.
        pass

    def set_key_image(self, key: int, image) -> None:
        if min(max(key, 0), self.KEY_COUNT - 1) != key:
            raise IndexError(f"Invalid key index {key}.")

        if key == self.PHANTOM_KEY:
            # No physical LCD at this grid slot (occupied by the stats window).
            return

        row, col = divmod(key, self.KEY_COLS)
        slot = f"{col}_{row}"

        with self._manifest_lock:
            if image is None:
                self._manifest.pop(slot, None)
                self._forget_icons_for_key(key)
            else:
                # The filename must change on every update, not just the
                # content: the device appears to cache a button's icon by
                # filename, so re-sending the same "<key>.png" name with
                # different bytes can leave the old image on screen even
                # though the new upload was accepted without error (see the
                # class docstring).
                self._icon_revision[key] = self._icon_revision.get(key, 0) + 1
                icon_name = f"{key}_{self._icon_revision[key]}.png"

                self._forget_icons_for_key(key, keep=icon_name)

                self._manifest[slot] = {
                    "State": 0,
                    "ViewParam": [{"Icon": f"icons/{icon_name}"}],
                }
                self._icons[icon_name] = bytes(image)

            # Callers typically update one key at a time, often many keys in
            # quick succession (e.g. on page load). Debounce so a burst of
            # updates results in a single combined multi-key upload instead
            # of one upload per key.
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            self._flush_timer = threading.Timer(self._FLUSH_DEBOUNCE_SECONDS, self._schedule_flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _forget_icons_for_key(self, key: int, keep: str | None = None) -> None:
        """Drop cached icon bytes for `key`'s previous revision(s) from
        self._icons, so the manifest we upload doesn't keep re-sending
        superseded images. Must be called with self._manifest_lock held."""
        prefix = f"{key}_"
        for stale_name in [name for name in self._icons if name.startswith(prefix) and name != keep]:
            self._icons.pop(stale_name, None)

    def _schedule_flush(self) -> None:
        """threading.Timer callback for the set_key_image() debounce (see
        that method): asks the send worker to flush the accumulated
        manifest/icons via _flush_buttons_now()."""
        self._send_queue.put((self._FLUSH_BUTTONS, b""))

    def _read_control_states(self):
        data = self.device.read(self._CHUNK_SIZE)
        if not data:
            return None

        if data[0:2] != b"\x7c\x7c":
            return None

        command = int.from_bytes(data[2:4], "big")
        if command != self._IN_BUTTON:
            return None

        # Button report layout (offsets within `data`, after the 8-byte
        # packet header): byte 1 = state (unused here), byte 2 = key index,
        # byte 3 = constant 0x01, byte 4 = pressed (0x01) / released (0x00).
        index = data[9]
        pressed = data[11] == 0x01

        if index >= self.PHYSICAL_KEY_COUNT:
            return None

        self._current_key_states[index] = pressed
        return {ControlType.KEY: list(self._current_key_states)}
