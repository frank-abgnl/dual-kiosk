#!/usr/bin/env python3
"""
Global Super Keyboard Shortcut Manager & IPC for Control Board Overlay.
Listens for 'Ctrl + Shift + M + S' globally across all XInput devices/seats,
identifies which seat/keyboard triggered the hotkey, and presents or launches
the Control Board GUI overlay on top of kiosk mode on that user's screen.
"""

import os
import sys
import time
import socket
import select
import ctypes
import ctypes.util
import logging
import threading
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("xinput_seating.hotkey")

from core.paths import SOCKET_PATH
PROJECT_DIR = Path(__file__).resolve().parent.parent
MAIN_PY = PROJECT_DIR / "main.py"


# ---------------------------------------------------------------------------
# IPC Helpers (Unix Domain Socket)
# ---------------------------------------------------------------------------
def send_overlay_command(cmd: str, timeout: float = 1.0) -> bool:
    """
    Sends an overlay command (e.g. 'SHOW seat1', 'TOGGLE seat2', 'HIDE')
    to an already-running Control Board GUI via Unix domain socket.
    Returns True if successfully delivered, False otherwise.
    """
    if not SOCKET_PATH.exists():
        return False

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(SOCKET_PATH))
            payload = f"{cmd.strip()}\n".encode("utf-8")
            client.sendall(payload)
            # Read optional response
            try:
                resp = client.recv(64).decode("utf-8").strip()
                logger.info(f"IPC sent '{cmd}', response: '{resp}'")
            except Exception:
                pass
            return True
    except Exception as exc:
        logger.debug(f"IPC send_overlay_command failed: {exc}")
        # Stale socket cleanup if refused
        if isinstance(exc, (ConnectionRefusedError, FileNotFoundError)):
            try:
                SOCKET_PATH.unlink(missing_ok=True)
            except Exception:
                pass
        return False


class OverlayIPCServer:
    """
    Lightweight Unix domain socket server running inside the Control Board GUI
    to receive overlay show/hide/toggle commands from global hotkey listeners or CLI.
    """

    def __init__(self, on_command: Callable[[str], None]):
        self.on_command = on_command
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            SOCKET_PATH.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.bind(str(SOCKET_PATH))
            self._sock.listen(5)
            self._sock.setblocking(False)
        except Exception as exc:
            logger.error(f"Failed to bind overlay IPC socket at {SOCKET_PATH}: {exc}")
            self._sock = None
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._server_loop, daemon=True, name="OverlayIPCServer")
        self._thread.start()
        logger.info(f"Overlay IPC server listening on {SOCKET_PATH}")

    def stop(self):
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        try:
            SOCKET_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("Overlay IPC server stopped.")

    def _server_loop(self):
        while not self._stop_event.is_set():
            if not self._sock:
                break
            try:
                r, _, _ = select.select([self._sock], [], [], 0.5)
                if not r or self._stop_event.is_set():
                    continue

                conn, _ = self._sock.accept()
                with conn:
                    conn.settimeout(1.0)
                    data = conn.recv(1024).decode("utf-8", errors="ignore").strip()
                    if data:
                        logger.info(f"IPC server received command: {data}")
                        try:
                            self.on_command(data)
                            conn.sendall(b"OK\n")
                        except Exception as exc:
                            logger.error(f"Error handling IPC command '{data}': {exc}")
                            conn.sendall(b"ERR\n")
            except (BlockingIOError, socket.error):
                continue
            except Exception as exc:
                if not self._stop_event.is_set():
                    logger.warning(f"Overlay IPC server loop exception: {exc}")


# ---------------------------------------------------------------------------
# Seat & Monitor Geometry Resolution
# ---------------------------------------------------------------------------
def resolve_seat_for_device(
    master_id: int,
    slave_id: int,
    seats: Optional[List[Dict]] = None,
    seat_keyboards: Optional[Dict[str, int]] = None,
) -> str:
    """
    Determines which seat triggered a hotkey event based on the master keyboard ID,
    slave device ID, USB hierarchy, or seat configurations.
    """
    if seats is None:
        try:
            from core.config_manager import ConfigManager
            seats = ConfigManager().load_config().get("seats", [])
        except Exception:
            seats = []

    # 1. Match active master keyboard ID
    if seat_keyboards:
        for sid, kbd_id in seat_keyboards.items():
            if kbd_id == master_id:
                return sid

    # 2. Match configured slave keyboard ID
    for s in seats:
        if s.get("keyboard_id") == slave_id:
            return s.get("id", "seat1")

    # 3. Match USB parent node
    try:
        from core.xinput_seating import XInputSeatingEngine
        parent = XInputSeatingEngine._usb_parent(slave_id)
        if parent:
            for s in seats:
                sid = s.get("keyboard_id")
                if sid and XInputSeatingEngine._usb_parent(sid) == parent:
                    return s.get("id", "seat1")
    except Exception:
        pass

    # 4. Check if master ID is not Virtual core keyboard (3)
    if master_id != 3 and len(seats) > 1:
        return seats[1].get("id", "seat2")

    return seats[0].get("id", "seat1") if seats else "seat1"


def get_seat_screen_geometry(
    seat_id: str,
    seats: Optional[List[Dict]] = None,
    displays: Optional[List[Dict]] = None,
) -> Tuple[int, int, int, int, str]:
    """
    Finds the (x, y, width, height, display_name) for the monitor assigned to seat_id.
    Falls back gracefully to display order or total screen if unassigned.
    """
    if seats is None:
        try:
            from core.config_manager import ConfigManager
            seats = ConfigManager().load_config().get("seats", [])
        except Exception:
            seats = []

    if displays is None:
        try:
            from core.device_manager import DeviceManager
            displays, _ = DeviceManager().get_displays()
        except Exception:
            displays = []

    seat_cfg = next((s for s in seats if s.get("id") == seat_id), None)
    target_display_name = seat_cfg.get("display") if seat_cfg else None

    # Search display by name
    if target_display_name and displays:
        for d in displays:
            if d.get("name") == target_display_name and d.get("width", 0) > 0:
                return d["x"], d["y"], d["width"], d["height"], d["name"]

    # Fallback by seat index
    seat_idx = 1 if seat_id == "seat2" else 0
    if displays and len(displays) > seat_idx and displays[seat_idx].get("width", 0) > 0:
        d = displays[seat_idx]
        return d["x"], d["y"], d["width"], d["height"], d["name"]

    if displays and len(displays) > 0 and displays[0].get("width", 0) > 0:
        d = displays[0]
        return d["x"], d["y"], d["width"], d["height"], d["name"]

    # Absolute fallback
    return 0, 0, 1024, 768, "default"


# ---------------------------------------------------------------------------
# XInput 2 Ctypes Structures
# ---------------------------------------------------------------------------
class _XIEventMask(ctypes.Structure):
    _fields_ = [
        ("deviceid", ctypes.c_int),
        ("mask_len", ctypes.c_int),
        ("mask", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _XGenericEventCookie(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("extension", ctypes.c_int),
        ("evtype", ctypes.c_int),
        ("cookie", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    ]


class _XIRawEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("extension", ctypes.c_int),
        ("evtype", ctypes.c_int),
        ("time", ctypes.c_ulong),
        ("deviceid", ctypes.c_int),
        ("sourceid", ctypes.c_int),
        ("detail", ctypes.c_int),  # Keycode
        ("flags", ctypes.c_int),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("xcookie", _XGenericEventCookie),
        ("pad", ctypes.c_long * 24),
    ]


# ---------------------------------------------------------------------------
# Global Super Keyboard Shortcut Manager
# ---------------------------------------------------------------------------
class HotkeyManager:
    """
    Listens for the super shortcut 'Ctrl + Shift + M + S' globally across all
    XInput keyboards without blocking other applications or conflicting with window grabs.
    """

    XI_RawKeyPress = 13
    XI_RawKeyRelease = 14
    XIAllMasterDevices = 1
    XIAllDevices = 0

    def __init__(
        self,
        on_trigger: Optional[Callable[[str, int, int], None]] = None,
        on_device_changed: Optional[Callable[[], None]] = None,
        launch_gui_if_needed: bool = False,
        seat_keyboards: Optional[Dict[str, int]] = None,
    ):
        """
        Args:
            on_trigger: Optional callback(seat_id, master_id, slave_id) when shortcut fires.
            on_device_changed: Optional callback() when devices are plugged or unplugged.
            launch_gui_if_needed: If True and no running GUI responds to IPC, launch the GUI process.
            seat_keyboards: Optional map of seat_id -> master keyboard ID.
        """
        self.on_trigger = on_trigger
        self.on_device_changed = on_device_changed
        self.launch_gui_if_needed = launch_gui_if_needed
        self.seat_keyboards = seat_keyboards or {}

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Key state tracking per input source/device
        self._pressed_keys: Dict[int, Set[str]] = {}
        self._last_trigger_time: float = 0.0
        self._debounce_window_sec: float = 0.8
        self._combo_held: bool = False

        # Key mapping caches
        self._ctrl_keycodes: Set[int] = {37, 105}
        self._shift_keycodes: Set[int] = {50, 62}
        self._m_keycodes: Set[int] = {58}
        self._s_keycodes: Set[int] = {39}

    def start(self):
        """Starts the global hotkey listener background thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listener_loop,
            daemon=True,
            name="SuperHotkeyListener",
        )
        self._thread.start()
        logger.info("Global Super Keyboard Shortcut (Ctrl+Shift+M+S) listener started.")

    def stop(self):
        """Stops the global hotkey listener background thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None
        logger.info("Global Super Keyboard Shortcut listener stopped.")

    def update_seat_keyboards(self, seat_keyboards: Dict[str, int]):
        """Updates known master keyboard IDs for each seat."""
        self.seat_keyboards = dict(seat_keyboards)

    def _resolve_dynamic_keycodes(self, x11, dpy):
        """Resolves keycodes for Ctrl, Shift, M, and S using current XKB keysym tables."""
        try:
            x11.XStringToKeysym.restype = ctypes.c_ulong
            x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
            x11.XKeysymToKeycode.restype = ctypes.c_ubyte
            x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

            def get_kc(sym_bytes):
                ks = x11.XStringToKeysym(sym_bytes)
                if ks:
                    kc = x11.XKeysymToKeycode(dpy, ks)
                    if kc:
                        return kc
                return None

            for sym, dest in [
                (b"Control_L", self._ctrl_keycodes),
                (b"Control_R", self._ctrl_keycodes),
                (b"Shift_L", self._shift_keycodes),
                (b"Shift_R", self._shift_keycodes),
                (b"m", self._m_keycodes),
                (b"M", self._m_keycodes),
                (b"s", self._s_keycodes),
                (b"S", self._s_keycodes),
            ]:
                kc = get_kc(sym)
                if kc:
                    dest.add(kc)
        except Exception as exc:
            logger.debug(f"Dynamic keycode resolution note: {exc}")

    def _classify_key(self, keycode: int, x11=None, dpy=None) -> Optional[str]:
        """Classifies a keycode into 'CTRL', 'SHIFT', 'M', 'S', or None."""
        if keycode in self._ctrl_keycodes:
            return "CTRL"
        if keycode in self._shift_keycodes:
            return "SHIFT"
        if keycode in self._m_keycodes:
            return "M"
        if keycode in self._s_keycodes:
            return "S"

        if x11 and dpy:
            try:
                for col in (0, 1):
                    ks = x11.XKeycodeToKeysym(dpy, keycode, col)
                    if ks in (65507, 65508):  # Control_L, Control_R
                        self._ctrl_keycodes.add(keycode)
                        return "CTRL"
                    if ks in (65505, 65506):  # Shift_L, Shift_R
                        self._shift_keycodes.add(keycode)
                        return "SHIFT"
                    if ks in (109, 77):  # m, M
                        self._m_keycodes.add(keycode)
                        return "M"
                    if ks in (115, 83):  # s, S
                        self._s_keycodes.add(keycode)
                        return "S"
            except Exception:
                pass

        return None

    def _listener_loop(self):
        """XInput 2 raw event polling loop."""
        try:
            x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xi = ctypes.CDLL(ctypes.util.find_library("Xi") or "libXi.so.6")

            x11.XInitThreads.restype = ctypes.c_int
            x11.XInitThreads()

            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            dpy = x11.XOpenDisplay(None)
            if not dpy:
                logger.error("Hotkey listener: Cannot open X11 Display.")
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

            xi.XISelectEvents.restype = ctypes.c_int
            xi.XISelectEvents.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(_XIEventMask),
                ctypes.c_int,
            ]

            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XFlush.argtypes = [ctypes.c_void_p]

            x11.XQueryExtension.restype = ctypes.c_int
            x11.XQueryExtension.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
            ]

            # Query XInput extension opcode
            xi_opcode = ctypes.c_int(0)
            ext_ok = x11.XQueryExtension(
                dpy,
                b"XInputExtension",
                ctypes.byref(xi_opcode),
                ctypes.byref(ctypes.c_int()),
                ctypes.byref(ctypes.c_int()),
            )
            if not ext_ok:
                logger.error("XInputExtension not supported on this display server.")
                x11.XCloseDisplay(dpy)
                return

            self._resolve_dynamic_keycodes(x11, dpy)

            # Mask 1: RawKeyPress (13) & RawKeyRelease (14) on XIAllMasterDevices (1)
            mask1_bytes = (ctypes.c_ubyte * 3)()
            mask1_bytes[1] |= (1 << (self.XI_RawKeyPress & 7)) | (1 << (self.XI_RawKeyRelease & 7))
            m1 = _XIEventMask()
            m1.deviceid = self.XIAllMasterDevices
            m1.mask_len = 3
            m1.mask = ctypes.cast(mask1_bytes, ctypes.POINTER(ctypes.c_ubyte))

            # Mask 2: XI_HierarchyChanged (11) on XIAllDevices (0) for hotplug detection
            mask2_bytes = (ctypes.c_ubyte * 3)()
            mask2_bytes[1] |= (1 << (11 & 7))
            m2 = _XIEventMask()
            m2.deviceid = self.XIAllDevices
            m2.mask_len = 3
            m2.mask = ctypes.cast(mask2_bytes, ctypes.POINTER(ctypes.c_ubyte))

            masks = (_XIEventMask * 2)(m1, m2)
            rc = xi.XISelectEvents(dpy, root, masks, 2)
            x11.XFlush(dpy)
            if rc != 0:
                logger.warning(f"XISelectEvents returned code {rc}")

            evt = _XEvent()

            while not self._stop_event.is_set():
                r, _, _ = select.select([fd], [], [], 0.3)
                if not r or self._stop_event.is_set():
                    continue

                while x11.XPending(dpy) > 0:
                    if self._stop_event.is_set():
                        break

                    x11.XNextEvent(dpy, ctypes.byref(evt))
                    if evt.type != 35:  # GenericEvent
                        continue

                    cookie = evt.xcookie
                    if cookie.extension != xi_opcode.value:
                        continue

                    if not x11.XGetEventData(dpy, ctypes.byref(cookie)):
                        continue

                    try:
                        evtype = cookie.evtype
                        if evtype == 11:  # XI_HierarchyChanged
                            logger.info("XInput HierarchyChanged event detected (device hotplug/unplug).")
                            if self.on_device_changed:
                                try:
                                    self.on_device_changed()
                                except Exception as exc:
                                    logger.warning(f"Error in on_device_changed callback: {exc}")
                            continue

                        raw = ctypes.cast(cookie.data, ctypes.POINTER(_XIRawEvent)).contents
                        master_id = raw.deviceid
                        slave_id = raw.sourceid
                        keycode = raw.detail

                        # Group by master device ID (and slave)
                        device_key = master_id
                        if device_key not in self._pressed_keys:
                            self._pressed_keys[device_key] = set()

                        group = self._classify_key(keycode, x11, dpy)

                        if evtype == self.XI_RawKeyPress:
                            if group:
                                self._pressed_keys[device_key].add(group)

                            # Check if all four keys are currently held on this device
                            current = self._pressed_keys[device_key]
                            if {"CTRL", "SHIFT", "M", "S"}.issubset(current):
                                now = time.time()
                                if (now - self._last_trigger_time > self._debounce_window_sec) and not self._combo_held:
                                    self._last_trigger_time = now
                                    self._combo_held = True
                                    self._handle_shortcut_trigger(master_id, slave_id)

                        elif evtype == self.XI_RawKeyRelease:
                            if group:
                                self._pressed_keys[device_key].discard(group)
                            # Release combo held state when any of the 4 keys is released
                            if not {"CTRL", "SHIFT", "M", "S"}.issubset(self._pressed_keys[device_key]):
                                self._combo_held = False

                    finally:
                        x11.XFreeEventData(dpy, ctypes.byref(cookie))

            x11.XCloseDisplay(dpy)
        except Exception as exc:
            if not self._stop_event.is_set():
                logger.error(f"Fatal error in hotkey listener: {exc}", exc_info=True)

    def _handle_shortcut_trigger(self, master_id: int, slave_id: int):
        """Called when Ctrl + Shift + M + S is hit on an input device."""
        seat_id = resolve_seat_for_device(master_id, slave_id, seat_keyboards=self.seat_keyboards)
        logger.info(
            f"Super Shortcut triggered! Master={master_id}, Slave={slave_id} -> Seat '{seat_id}'"
        )

        # 1. If explicit callback registered, invoke it
        if self.on_trigger:
            try:
                self.on_trigger(seat_id, master_id, slave_id)
                return
            except Exception as exc:
                logger.error(f"Error in on_trigger callback: {exc}")

        # 2. Try sending command to running GUI via IPC
        delivered = send_overlay_command(f"TOGGLE {seat_id}")
        if delivered:
            logger.info(f"Delivered TOGGLE command for {seat_id} to active GUI.")
            return

        # 3. If GUI is not running and launch_gui_if_needed is enabled, spawn it
        if self.launch_gui_if_needed:
            logger.info(f"GUI not running; launching overlay process on screen for {seat_id}...")
            self._spawn_overlay_gui(seat_id)

    def _spawn_overlay_gui(self, seat_id: str):
        """Spawns the Control Board GUI in overlay mode on the target seat's screen."""
        try:
            cmd = [
                sys.executable,
                str(MAIN_PY),
                "--overlay",
                "--seat", seat_id,
            ]
            env = os.environ.copy()
            env.pop("WAYLAND_DISPLAY", None)
            env["MOZ_ENABLE_WAYLAND"] = "0"
            subprocess.Popen(
                cmd,
                env=env,
                start_new_session=True,
            )
            logger.info(f"Spawned Control Board GUI overlay for {seat_id}.")
        except Exception as exc:
            logger.error(f"Failed to spawn Control Board GUI overlay: {exc}")
