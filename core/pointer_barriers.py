#!/usr/bin/env python3
"""
Per-master pointer confinement using XFixes pointer barriers.

Why this exists: a Coordinate Transformation Matrix only offsets *absolute*
devices (tablets, touchscreens). A normal relative mouse ignores the offset, so
the master pointer roams across the whole desktop. Pointer barriers are applied
per master device and genuinely stop a cursor at a monitor edge.

Barriers belong to the X connection that created them, so they live exactly as
long as this process (GUI or `--start` CLI) stays running.
"""

import ctypes
import ctypes.util
import logging
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("xinput_seating.barriers")

# XFixes "directions" = directions in which the barrier does NOT block.
POSITIVE_X, NEGATIVE_X, POSITIVE_Y, NEGATIVE_Y = 1, 2, 4, 8


class PointerConfiner:
    def __init__(self):
        self._lock = threading.RLock()
        self._dpy = None
        self._root = None
        self._x11 = self._xfixes = self._xi = None
        self._barriers: Dict[int, List[int]] = {}

    # ------------------------------------------------------------------
    def _open(self) -> bool:
        if self._dpy:
            return True
        try:
            x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xfixes = ctypes.CDLL(ctypes.util.find_library("Xfixes") or "libXfixes.so.3")
            xi = ctypes.CDLL(ctypes.util.find_library("Xi") or "libXi.so.6")

            x11.XInitThreads.restype = ctypes.c_int
            x11.XInitThreads()
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XFlush.argtypes = [ctypes.c_void_p]
            x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]

            xfixes.XFixesQueryVersion.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
            xfixes.XFixesCreatePointerBarrier.restype = ctypes.c_ulong
            xfixes.XFixesCreatePointerBarrier.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
            ]
            xfixes.XFixesDestroyPointerBarrier.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

            xi.XIWarpPointer.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.c_double, ctypes.c_double, ctypes.c_uint, ctypes.c_uint,
                ctypes.c_double, ctypes.c_double,
            ]

            xi.XISetClientPointer.restype = ctypes.c_int
            xi.XISetClientPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
            xi.XISetFocus.restype = ctypes.c_int
            xi.XISetFocus.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong]

            dpy = x11.XOpenDisplay(None)
            if not dpy:
                logger.error("Pointer barriers: cannot open X display (is DISPLAY set / are you on X11?).")
                return False

            major, minor = ctypes.c_int(0), ctypes.c_int(0)
            xfixes.XFixesQueryVersion(dpy, ctypes.byref(major), ctypes.byref(minor))
            if major.value < 5:
                logger.error(f"XFixes {major.value}.{minor.value} too old; pointer barriers need >= 5.0.")
                return False

            self._x11, self._xfixes, self._xi = x11, xfixes, xi
            self._dpy = dpy
            self._root = x11.XDefaultRootWindow(dpy)
            return True
        except Exception as exc:
            logger.error(f"Pointer barriers unavailable: {exc}")
            return False

    # ------------------------------------------------------------------
    def release(self, master_id: int):
        with self._lock:
            ids = self._barriers.pop(master_id, [])
            if not ids or not self._dpy:
                return
            for b in ids:
                self._xfixes.XFixesDestroyPointerBarrier(self._dpy, b)
            self._x11.XFlush(self._dpy)

    def set_client_pointer(self, window_id, master_pointer_id: int) -> bool:
        """
        Sets the *client pointer* of whichever X client owns `window_id`.
        A client's plain XSetInputFocus/pointer requests act on its client pointer's
        paired keyboard. Left at the default (Virtual core), a newly opened Firefox
        steals the core keyboard's focus; binding it to its own seat's master stops that.
        """
        try:
            win = int(str(window_id), 0)
        except ValueError:
            return False
        with self._lock:
            if not self._open():
                return False
            self._xi.XISetClientPointer(self._dpy, win, int(master_pointer_id))
            self._x11.XFlush(self._dpy)
            return True

    def set_focus(self, keyboard_master_id: int, window_id) -> bool:
        """
        Gives a specific MASTER KEYBOARD input focus on a window (XISetFocus).
        Window managers only manage focus for the core keyboard, so every extra
        seat keyboard needs this or its keystrokes have nowhere to go.
        """
        try:
            win = int(str(window_id), 0)
        except ValueError:
            return False
        with self._lock:
            if not self._open():
                return False
            self._xi.XISetFocus(self._dpy, int(keyboard_master_id), win, 0)  # 0 = CurrentTime
            self._x11.XFlush(self._dpy)
            return True

    def release_all(self):
        with self._lock:
            for mid in list(self._barriers.keys()):
                self.release(mid)

    def confine(
        self,
        master_id: int,
        x: int, y: int, w: int, h: int,
        total_screen: Optional[Tuple[int, int]] = None,
    ) -> bool:
        """Locks the given master pointer inside the rectangle (x, y, w, h)."""
        with self._lock:
            if not self._open():
                return False
            self.release(master_id)

            # Move the cursor into the target monitor first (barriers don't affect warps).
            self._xi.XIWarpPointer(self._dpy, master_id, 0, self._root,
                                   0.0, 0.0, 0, 0, float(x + w // 2), float(y + h // 2))

            tw, th = total_screen or (0, 0)
            edges = []
            if x > 0:
                edges.append((x, y, x, y + h, POSITIVE_X))            # left edge: allow entering
            if tw == 0 or x + w < tw:
                edges.append((x + w, y, x + w, y + h, NEGATIVE_X))    # right edge
            if y > 0:
                edges.append((x, y, x + w, y, POSITIVE_Y))            # top edge
            if th == 0 or y + h < th:
                edges.append((x, y + h, x + w, y + h, NEGATIVE_Y))    # bottom edge

            devs = (ctypes.c_int * 1)(master_id)
            created = []
            for x1, y1, x2, y2, allowed in edges:
                bid = self._xfixes.XFixesCreatePointerBarrier(
                    self._dpy, self._root, x1, y1, x2, y2, allowed, 1, devs)
                created.append(bid)
            self._barriers[master_id] = created
            self._x11.XSync(self._dpy, 0)
            logger.info(f"Confined master pointer {master_id} to {w}x{h}+{x}+{y} ({len(created)} barriers).")
            return True
