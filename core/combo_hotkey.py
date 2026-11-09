#!/usr/bin/env python3
"""
Global Ctrl+Shift+M+<letter> shortcut listener (XInput 2 raw events, passive: it never
grabs or swallows keys). Used for Ctrl+Shift+M+R = "re-seat everything, no GUI".

Raw events from every master keyboard are watched, so it works from any seat while a
fullscreen kiosk has the focus.
"""

import ctypes
import ctypes.util
import logging
import select
import threading
import time
from typing import Callable, Dict, Optional, Set

from core.hotkey_manager import (_XEvent, _XGenericEventCookie, _XIEventMask, _XIRawEvent,
                                 resolve_seat_for_device)

logger = logging.getLogger("xinput_seating.combo")

# evdev+xkb keycodes (US layout defaults); real values are resolved from the live keymap
DEFAULT_KEYCODES = {"CTRL": {37, 105}, "SHIFT": {50, 62}, "M": {58}}
LETTER_DEFAULT = {"r": {27}, "s": {39}}


class ComboTracker:
    """Pure state machine: feed it key press/release per device, it says when the combo fires."""

    def __init__(self, required=("CTRL", "SHIFT", "M", "KEY"), debounce: float = 0.8):
        self.required = set(required)
        self.debounce = debounce
        self._pressed: Dict[int, Set[str]] = {}
        self._held = False
        self._last = 0.0

    def event(self, device: int, group: Optional[str], pressed: bool, now: Optional[float] = None) -> bool:
        keys = self._pressed.setdefault(device, set())
        now = time.time() if now is None else now
        if pressed:
            if group:
                keys.add(group)
            if self.required <= keys and not self._held and now - self._last > self.debounce:
                self._held, self._last = True, now
                return True
        else:
            if group:
                keys.discard(group)
            if not self.required <= keys:
                self._held = False
        return False


class ComboHotkey:
    XI_RawKeyPress, XI_RawKeyRelease, XIAllMasterDevices = 13, 14, 1

    def __init__(self, letter: str, on_trigger: Callable[[str, int, int], None],
                 seat_keyboards: Optional[Dict[str, int]] = None, name: str = "combo"):
        self.letter = letter.lower()
        self.on_trigger = on_trigger
        self.seat_keyboards = seat_keyboards or {}
        self.name = name
        self.tracker = ComboTracker()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._codes = {k: set(v) for k, v in DEFAULT_KEYCODES.items()}
        self._codes["KEY"] = set(LETTER_DEFAULT.get(self.letter, set()))

    def update_seat_keyboards(self, seat_keyboards: Dict[str, int]):
        self.seat_keyboards = dict(seat_keyboards)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"Hotkey-{self.name}")
        self._thread.start()
        logger.info(f"Ctrl+Shift+M+{self.letter.upper()} listener started ({self.name}).")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None

    def _classify(self, x11, dpy, keycode: int) -> Optional[str]:
        for group, codes in self._codes.items():
            if keycode in codes:
                return group
        try:
            for col in (0, 1):
                ks = x11.XKeycodeToKeysym(dpy, keycode, col)
                if ks in (65507, 65508):
                    self._codes["CTRL"].add(keycode); return "CTRL"
                if ks in (65505, 65506):
                    self._codes["SHIFT"].add(keycode); return "SHIFT"
                if ks in (109, 77):
                    self._codes["M"].add(keycode); return "M"
                if ks in (ord(self.letter), ord(self.letter.upper())):
                    self._codes["KEY"].add(keycode); return "KEY"
        except Exception:
            pass
        return None

    def _loop(self):
        try:
            x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xi = ctypes.CDLL(ctypes.util.find_library("Xi") or "libXi.so.6")
            x11.XInitThreads()
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            dpy = x11.XOpenDisplay(None)
            if not dpy:
                logger.error("Combo hotkey: cannot open X display.")
                return
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            root = x11.XDefaultRootWindow(dpy)
            x11.XConnectionNumber.restype = ctypes.c_int
            x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
            fd = x11.XConnectionNumber(dpy)
            x11.XPending.restype = ctypes.c_int
            x11.XPending.argtypes = [ctypes.c_void_p]
            x11.XKeycodeToKeysym.restype = ctypes.c_ulong
            x11.XKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int]
            x11.XGetEventData.restype = ctypes.c_int
            x11.XGetEventData.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XGenericEventCookie)]
            x11.XFreeEventData.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XGenericEventCookie)]
            x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
            x11.XFlush.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XQueryExtension.restype = ctypes.c_int
            x11.XQueryExtension.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
                                            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
            xi.XISelectEvents.restype = ctypes.c_int
            xi.XISelectEvents.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XIEventMask), ctypes.c_int]

            opcode = ctypes.c_int(0)
            if not x11.XQueryExtension(dpy, b"XInputExtension", ctypes.byref(opcode),
                                       ctypes.byref(ctypes.c_int()), ctypes.byref(ctypes.c_int())):
                logger.error("XInputExtension not available.")
                x11.XCloseDisplay(dpy)
                return

            # Resolve the live keycodes of Ctrl / Shift / M / <letter>
            x11.XStringToKeysym.restype = ctypes.c_ulong
            x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
            x11.XKeysymToKeycode.restype = ctypes.c_ubyte
            x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            for sym, group in ((b"Control_L", "CTRL"), (b"Control_R", "CTRL"), (b"Shift_L", "SHIFT"),
                               (b"Shift_R", "SHIFT"), (b"m", "M"), (self.letter.encode(), "KEY")):
                kc = x11.XKeysymToKeycode(dpy, x11.XStringToKeysym(sym))
                if kc:
                    self._codes[group].add(kc)

            mask = (ctypes.c_ubyte * 3)()
            mask[1] |= (1 << (self.XI_RawKeyPress & 7)) | (1 << (self.XI_RawKeyRelease & 7))
            m = _XIEventMask()
            m.deviceid, m.mask_len = self.XIAllMasterDevices, 3
            m.mask = ctypes.cast(mask, ctypes.POINTER(ctypes.c_ubyte))
            xi.XISelectEvents(dpy, root, ctypes.byref(m), 1)
            x11.XFlush(dpy)

            evt = _XEvent()
            while not self._stop.is_set():
                r, _, _ = select.select([fd], [], [], 0.3)
                if not r:
                    continue
                while x11.XPending(dpy) > 0 and not self._stop.is_set():
                    x11.XNextEvent(dpy, ctypes.byref(evt))
                    if evt.type != 35:  # GenericEvent
                        continue
                    cookie = evt.xcookie
                    if cookie.extension != opcode.value or not x11.XGetEventData(dpy, ctypes.byref(cookie)):
                        continue
                    try:
                        raw = ctypes.cast(cookie.data, ctypes.POINTER(_XIRawEvent)).contents
                        pressed = cookie.evtype == self.XI_RawKeyPress
                        group = self._classify(x11, dpy, raw.detail)
                        if self.tracker.event(raw.deviceid, group, pressed):
                            self._fire(raw.deviceid, raw.sourceid)
                    finally:
                        x11.XFreeEventData(dpy, ctypes.byref(cookie))
            x11.XCloseDisplay(dpy)
        except Exception as exc:
            if not self._stop.is_set():
                logger.error(f"Combo hotkey listener failed: {exc}", exc_info=True)

    def _fire(self, master_id: int, slave_id: int):
        seat = resolve_seat_for_device(master_id, slave_id, seat_keyboards=self.seat_keyboards)
        logger.info(f"Ctrl+Shift+M+{self.letter.upper()} pressed on {seat} (master {master_id}).")
        try:
            self.on_trigger(seat, master_id, slave_id)
        except Exception as exc:
            logger.error(f"{self.name} trigger failed: {exc}")
