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
import logging
import threading
from typing import Dict, List, Optional, Tuple

from core.x11 import (
    load_x11,
    open_display,
    _XIButtonState,
    _XIModifierState,
    _XIGroupState,
)

logger = logging.getLogger("xinput_seating.barriers")

# XFixes "directions" = directions in which the barrier does NOT block.
POSITIVE_X, NEGATIVE_X, POSITIVE_Y, NEGATIVE_Y = 1, 2, 4, 8


def is_point_in_rect(px: float, py: float, rect: Tuple[int, int, int, int]) -> bool:
    """True if point (px, py) is strictly inside rect (x, y, w, h)."""
    rx, ry, rw, rh = rect
    return (rx <= px < rx + rw) and (ry <= py < ry + rh)


def compute_barrier_edges(
    x: int, y: int, w: int, h: int, total_screen: Optional[Tuple[int, int]] = None
) -> List[Tuple[int, int, int, int, int]]:
    """
    Computes barrier coordinates and allowed directions for a monitor rectangle.
    Barriers are only created for interior boundaries (adjacent to other screens);
    outer edges that touch the screen canvas boundary require no barriers.
    Returns list of (x1, y1, x2, y2, allowed_direction).
    """
    tw, th = total_screen or (0, 0)
    edges = []
    if x > 0:
        edges.append((x, y, x, y + h, POSITIVE_X))            # left edge: allow entering from left
    if tw == 0 or x + w < tw:
        edges.append((x + w, y, x + w, y + h, NEGATIVE_X))    # right edge: allow entering from right
    if y > 0:
        edges.append((x, y, x + w, y, POSITIVE_Y))            # top edge: allow entering from top
    if th == 0 or y + h < th:
        edges.append((x, y + h, x + w, y + h, NEGATIVE_Y))    # bottom edge: allow entering from bottom
    return edges


def nearest_point_in_rect(px: float, py: float, rect: Tuple[int, int, int, int]) -> Tuple[float, float]:
    """
    Clamps (px, py) to the nearest point inside rect=(x, y, w, h).
    Uses a 1-pixel interior margin so the pointer stays strictly inside boundaries.
    """
    rx, ry, rw, rh = rect
    min_x = float(rx)
    max_x = float(rx + max(0, rw - 1))
    min_y = float(ry)
    max_y = float(ry + max(0, rh - 1))

    clamped_x = min(max(px, min_x), max_x)
    clamped_y = min(max(py, min_y), max_y)
    return (clamped_x, clamped_y)


class ConfinementWatchdog:
    """
    Safety net thread that periodically checks all confined master pointers.
    If a pointer escaped its barrier (e.g. via fast mouse movement or WM event),
    it warps it back to the nearest point inside its monitor rectangle.
    If XIQueryPointer fails (e.g. BadDevice because master was removed),
    it drops that master from confinement.
    """

    def __init__(self, confiner: "PointerConfiner", interval: float = 0.25):
        self.confiner = confiner
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="ConfinementWatchdog", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            x11, xi, _ = load_x11()
            dpy = open_display()
            if not dpy:
                logger.debug("ConfinementWatchdog: Cannot open display")
                return
            root = x11.XDefaultRootWindow(dpy)
            try:
                while not self._stop_event.wait(self.interval):
                    self.check_all(dpy, root, x11, xi)
            finally:
                x11.XCloseDisplay(dpy)
        except Exception as exc:
            logger.debug(f"ConfinementWatchdog thread exit: {exc}")

    def query_pointer(self, dpy, root, xi, x11, master_id: int) -> Tuple[bool, float, float]:
        root_ret = ctypes.c_ulong()
        child_ret = ctypes.c_ulong()
        root_x = ctypes.c_double()
        root_y = ctypes.c_double()
        win_x = ctypes.c_double()
        win_y = ctypes.c_double()
        btn = _XIButtonState()
        mods = _XIModifierState()
        grp = _XIGroupState()

        res = xi.XIQueryPointer(
            dpy,
            int(master_id),
            root,
            ctypes.byref(root_ret),
            ctypes.byref(child_ret),
            ctypes.byref(root_x),
            ctypes.byref(root_y),
            ctypes.byref(win_x),
            ctypes.byref(win_y),
            ctypes.byref(btn),
            ctypes.byref(mods),
            ctypes.byref(grp),
        )
        if btn.mask:
            x11.XFree(btn.mask)

        if res == 0:
            return False, 0.0, 0.0
        return True, root_x.value, root_y.value

    def check_all(self, dpy, root, x11, xi):
        with self.confiner._lock:
            targets = dict(self.confiner._confined_rects)

        dropped = []
        for master_id, rect in targets.items():
            ok, px, py = self.query_pointer(dpy, root, xi, x11, master_id)
            if not ok:
                logger.warning(f"ConfinementWatchdog: master {master_id} returned error on query; dropping.")
                dropped.append(master_id)
                continue

            if not is_point_in_rect(px, py, rect):
                nx, ny = nearest_point_in_rect(px, py, rect)
                logger.debug(f"Master {master_id} escaped ({px}, {py}); warping back to ({nx}, {ny}) in {rect}")
                xi.XIWarpPointer(
                    dpy,
                    int(master_id),
                    0,
                    root,
                    0.0,
                    0.0,
                    0,
                    0,
                    float(nx),
                    float(ny),
                )
                x11.XFlush(dpy)

        if dropped:
            with self.confiner._lock:
                for mid in dropped:
                    self.confiner.release(mid)


class PointerConfiner:
    def __init__(self):
        self._lock = threading.RLock()
        self._dpy = None
        self._root = None
        self._x11 = self._xfixes = self._xi = None
        self._barriers: Dict[int, List[int]] = {}
        self._confined_rects: Dict[int, Tuple[int, int, int, int]] = {}
        self.watchdog = ConfinementWatchdog(self)

    def start_watchdog(self):
        self.watchdog.start()

    def stop_watchdog(self):
        self.watchdog.stop()

    # ------------------------------------------------------------------
    def _open(self) -> bool:
        if self._dpy:
            return True
        try:
            x11, xi, xfixes = load_x11()
            dpy = open_display()
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
            self._confined_rects.pop(master_id, None)
            ids = self._barriers.pop(master_id, [])
            if not ids or not self._dpy:
                return
            for b in ids:
                self._xfixes.XFixesDestroyPointerBarrier(self._dpy, b)
            self._x11.XFlush(self._dpy)
            if not self._confined_rects:
                self.stop_watchdog()

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
        self.stop_watchdog()
        with self._lock:
            self._confined_rects.clear()
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

            edges = compute_barrier_edges(x, y, w, h, total_screen)

            devs = (ctypes.c_int * 1)(master_id)
            created = []
            for x1, y1, x2, y2, allowed in edges:
                bid = self._xfixes.XFixesCreatePointerBarrier(
                    self._dpy, self._root, x1, y1, x2, y2, allowed, 1, devs)
                created.append(bid)
            self._barriers[master_id] = created
            self._confined_rects[master_id] = (x, y, w, h)
            self._x11.XSync(self._dpy, 0)
            logger.info(f"Confined master pointer {master_id} to {w}x{h}+{x}+{y} ({len(created)} barriers).")
            self.start_watchdog()
            return True
