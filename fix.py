from pathlib import Path

ROOT = Path(".").resolve()

# ---------------------------------------------------------------------------
# 1. core/session_manager.py
# ---------------------------------------------------------------------------
(ROOT / "core/session_manager.py").write_text('''#!/usr/bin/env python3
"""
Unified Session Manager for XInput Pseudo-Seating & Dual Firefox Kiosks.

Acts as the single authoritative start-up and shut-down wrapper across all entry points:
  - Desktop Shortcut (launch.sh / launcher.py)
  - Control Panel GUI (gui/app.py / dashboard_tab.py)
  - Command Line Interface (main.py --start / --stop)
  - Systemd / Autostart (main.py --auto-start)

Guarantees that a session started by ANY process can be monitored and cleanly
closed by ANY other process (including the GUI Control Board), with complete
termination of background supervisors, kiosks, and full restoration of XInput devices.
"""

import fcntl
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("xinput_seating.session")

CONFIG_DIR = Path.home() / ".config" / "xinput-seats"
PID_FILE = CONFIG_DIR / "launcher.pid"
LOCK_FILE = CONFIG_DIR / "launcher.lock"
LOG_FILE = CONFIG_DIR / "launcher.log"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _pid_alive(pid: int) -> bool:
    """True if pid exists and is not a zombie."""
    try:
        os.kill(pid, 0)
        with open(f"/proc/{pid}/stat", "r") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError, ValueError):
        return False


def get_supervisor_pid() -> Optional[int]:
    """Returns the PID of an active supervisor process if one is running."""
    try:
        if PID_FILE.exists():
            pid = int(PID_FILE.read_text().strip())
            if _pid_alive(pid):
                return pid
    except (OSError, ValueError):
        pass
    return None


class SessionManager:
    """
    Central wrapper for starting, stopping, restarting, and checking the status
    of the dual-kiosk multiseat session.
    """

    def __init__(self):
        from core.config_manager import ConfigManager
        from core.device_manager import DeviceManager
        from core.kiosk_manager import KioskManager
        from core.xinput_seating import XInputSeatingEngine
        from core.uninstaller import Uninstaller

        self.cfg_mgr = ConfigManager()
        self.dm = DeviceManager()
        self.engine = XInputSeatingEngine()
        self.kiosk = KioskManager()
        self.kiosk.on_window_ready = self.engine.focus_seat_keyboard
        self.kiosk.engine = self.engine
        self.uninstaller = Uninstaller()
        self.display_guard = None
        self.hotkey_mgr = None
        self.reseat_key = None

    # -----------------------------------------------------------------------
    # Status & Inspection
    # -----------------------------------------------------------------------
    @staticmethod
    def get_supervisor_pid() -> Optional[int]:
        return get_supervisor_pid()

    def find_all_kiosks(self) -> List[int]:
        """Finds all Firefox processes on the system running with our kiosk profiles."""
        kiosk_pids = set(self.uninstaller.find_kiosks())
        discovered = self.kiosk.discover_running_kiosks()
        for s in discovered.values():
            if s.get("pid"):
                kiosk_pids.add(s["pid"])
        return sorted(list(kiosk_pids))

    def is_session_active(self) -> bool:
        """True if either a supervisor is running or any kiosk browser is running."""
        return (get_supervisor_pid() is not None) or len(self.find_all_kiosks()) > 0

    # -----------------------------------------------------------------------
    # Runtime Services (DisplayGuard & Reseat Hotkey)
    # -----------------------------------------------------------------------
    def _start_runtime_services(self, on_event: Optional[Callable[[str], None]] = None):
        from core.display_guard import DisplayGuard
        from core.combo_hotkey import ComboHotkey

        if not self.display_guard or not self.display_guard.is_running():
            self.display_guard = DisplayGuard(self.kiosk, self.engine, interval=1.2, on_event=on_event)
            self.display_guard.start()

        if not self.reseat_key:
            self.reseat_key = ComboHotkey(
                "r",
                lambda *_: self.reseat_session(hard=False, on_event=on_event),
                seat_keyboards=self.engine.seat_keyboards,
                name="reseat",
            )
            self.reseat_key.start()
        else:
            self.reseat_key.update_seat_keyboards(self.engine.seat_keyboards)

    def _stop_runtime_services(self):
        if self.display_guard:
            try:
                self.display_guard.stop()
            except Exception:
                pass
            self.display_guard = None
        if self.reseat_key:
            try:
                self.reseat_key.stop()
            except Exception:
                pass
            self.reseat_key = None
        if self.hotkey_mgr:
            try:
                self.hotkey_mgr.stop()
            except Exception:
                pass
            self.hotkey_mgr = None

    # -----------------------------------------------------------------------
    # Unified Shut-Down Wrapper
    # -----------------------------------------------------------------------
    def stop_session(
        self,
        exit_gui: bool = False,
        on_event: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Unified shut-down wrapper.
        Stops background supervisor process, terminates all kiosk browsers,
        stops all local monitors, resets XInput devices to core, releases barriers,
        cleans up PID/lock files, and optionally closes the GUI.
        """
        def _emit(msg: str):
            logger.info(msg)
            if on_event:
                try:
                    on_event(msg)
                except Exception:
                    pass

        _emit("Stopping multiseat kiosk session...")

        # 0. Stop runtime services in this process
        self._stop_runtime_services()

        # 1. Notify running Control Board overlay via IPC
        try:
            from core.hotkey_manager import send_overlay_command
            if exit_gui:
                send_overlay_command("QUIT")
            else:
                send_overlay_command("HIDE")
        except Exception:
            pass

        # 2. Terminate background supervisor process (launcher.py / main.py --start)
        sup_pid = get_supervisor_pid()
        me = os.getpid()
        if sup_pid and sup_pid != me:
            _emit(f"Terminating background supervisor process (PID {sup_pid})...")
            try:
                os.kill(sup_pid, signal.SIGTERM)
                deadline = time.time() + 2.5
                while time.time() < deadline:
                    if not _pid_alive(sup_pid):
                        break
                    time.sleep(0.15)
                else:
                    os.kill(sup_pid, signal.SIGKILL)
            except OSError:
                pass

        # Also search for any other python supervisor instances
        try:
            other_sups = [p for p in self.uninstaller.find_supervisors() if p != me]
            if other_sups:
                _emit(f"Terminating other supervisor instances: {other_sups}")
                self.uninstaller._terminate(other_sups, "supervisors")
        except Exception:
            pass

        # 3. Stop local kiosk manager & watchdogs in this process
        try:
            self.kiosk.stop_all_kiosks()
        except Exception as exc:
            logger.debug(f"stop_all_kiosks notice: {exc}")

        # 4. Terminate ALL kiosk browser processes across the entire system
        kiosk_pids = self.find_all_kiosks()
        if kiosk_pids:
            _emit(f"Terminating all active kiosk browsers: {kiosk_pids}...")
            self.uninstaller._terminate(kiosk_pids, "kiosk browsers")

        # Double check with pkill -f for isolated profile dirs
        try:
            prof_dir = str(Path.home() / ".config" / "xinput-seats" / "profiles")
            subprocess.run(["pkill", "-9", "-f", prof_dir], capture_output=True)
        except Exception:
            pass

        # 5. Reset all XInput devices to Virtual core and remove custom master pairs
        _emit("Resetting all XInput devices to Virtual core...")
        try:
            self.engine.reset_all_to_core()
        except Exception as exc:
            logger.warning(f"Engine reset_all_to_core error: {exc}")

        # 6. Release any remaining pointer barriers
        try:
            self.engine.confiner.release_all()
        except Exception as exc:
            logger.debug(f"Confiner release_all notice: {exc}")

        # 7. Clean up PID, lock, and overlay state
        try:
            from core import overlay_state
            overlay_state.clear()
        except Exception:
            pass

        try:
            PID_FILE.unlink(missing_ok=True)
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass

        _emit("Kiosks stopped and input devices returned to normal desktop.")
        return True

    # -----------------------------------------------------------------------
    # Unified Start-Up Wrapper
    # -----------------------------------------------------------------------
    def start_session(
        self,
        reassign: bool = False,
        no_detect: bool = False,
        on_event: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Unified start-up wrapper.
        Validates displays (modal with Retry if < 2 displays), assigns seats respecting
        preference & priority rules, applies seating, launches dual kiosks, and starts
        supervisors.
        """
        def _emit(msg: str):
            logger.info(msg)
            if on_event:
                try:
                    on_event(msg)
                except Exception:
                    pass

        _emit("Detecting hardware and validating displays...")

        # Stop existing session first if reassign requested or stale session exists
        if reassign or not self.is_session_active():
            self.stop_session(exit_gui=False)
            time.sleep(0.5)

        # 1. Automatic Display Detection: Require at least 2 connected active displays
        active_displays, total = self.dm.probe_and_activate_displays()
        if len(active_displays) < 2:
            disp_name = active_displays[0]["name"] if active_displays else "Unknown"
            _emit(f"Only 1 display detected ({disp_name}). Dual-seat kiosk requires 2 monitors.")
            from gui.display_dialog import run_single_display_error_modal
            ok_retry, new_disp = run_single_display_error_modal(active_displays)
            if not ok_retry or not new_disp or len(new_disp) < 2:
                _emit("Launch aborted: Second display required.")
                return False, "Dual kiosks require at least two connected displays."
            active_displays = new_disp
            total = self.dm.get_displays()[1]

        displays = active_displays

        # 2. Hardware / seat detection & assignment with preference and priority rules
        cfg = self.cfg_mgr.load_config() if (no_detect and self.cfg_mgr.config_path.exists()) else {}
        seats = cfg.get("seats") or []
        has_memory = len(seats) >= 2 and any(s.get("mouse_name") or s.get("keyboard_name") for s in seats[:2])

        if reassign or not has_memory:
            _emit("Detecting hardware and assigning seats from scratch...")
            cfg = self.cfg_mgr.auto_generate_config(self.dm, force_reassign=True)
        else:
            _emit("Using saved seat assignment (peripherals matched by USB identity/memory)...")

        # Automatically assign displays to seats with priority and preference rules
        seats, assign_msgs = self.dm.assign_displays_to_seats(cfg.get("seats", []), displays)
        for m in assign_msgs:
            _emit(f"Display assignment: {m}")
        cfg["seats"] = seats

        # Resolve live devices with PeripheralResolver
        resolved, assignments = self.engine.resolve_live(cfg["seats"])
        for s in resolved:
            s.pop("pointer_connected", None)
            s.pop("keyboard_connected", None)
        cfg["seats"] = resolved
        self.cfg_mgr.save_config(cfg)

        for (sid, cls), dev in sorted(assignments.items()):
            seat = next((x for x in cfg["seats"] if x.get("id") == sid), {})
            _emit(f"  {sid} {cls}: " + (f"{dev['name']} (ID {dev['id']})" if dev else "not connected")
                  + f"   display={seat.get('display')}")

        # 3. Apply XInput pseudo-seating (MPX masters + confinement)
        _emit("Applying XInput MPX master pairs and device reattachments...")
        results = self.engine.apply_seating(seats, displays, (total["width"], total["height"]))
        _emit(f"Seating applied: {results}")

        # 4. Launch dual Firefox kiosks
        _emit("Launching dual Firefox kiosks with isolated profiles...")
        s1_url = seats[0].get("kiosk_url") if len(seats) > 0 else "https://duckduckgo.com"
        s2_url = seats[1].get("kiosk_url") if len(seats) > 1 else "https://www.google.com"

        ok = self.kiosk.launch_dual_kiosks(
            seat1_url=s1_url or "https://duckduckgo.com",
            seat2_url=s2_url or "https://www.google.com",
            displays_info=displays,
            enforce_lock=True,
        )

        if ok:
            PID_FILE.write_text(str(os.getpid()))
            self._start_runtime_services(on_event=on_event)
            _emit("Dual Firefox kiosks launched successfully!")
            return True, "Dual Firefox kiosks running."
        else:
            _emit("Failed to launch one or more Firefox kiosks.")
            return False, "Failed to launch Firefox kiosks."

    def restart_session(
        self,
        reassign: bool = False,
        on_event: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """Stops any running session (without exiting the GUI) and starts a fresh session."""
        self.stop_session(exit_gui=False, on_event=on_event)
        time.sleep(0.5)
        return self.start_session(reassign=reassign, no_detect=False, on_event=on_event)

    def reseat_session(
        self,
        hard: bool = False,
        on_event: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Re-seats all devices of a running session (soft = keep browsers, hard = restart browsers)."""
        def _emit(msg: str):
            logger.info(msg)
            if on_event:
                try:
                    on_event(msg)
                except Exception:
                    pass

        try:
            from core.hotkey_manager import send_overlay_command
            send_overlay_command("HIDE")
            alive = (
                len(self.kiosk.running_processes) >= 2
                and all(p.poll() is None for p in self.kiosk.running_processes.values())
            )
            if hard or not alive:
                ok, _ = self.restart_session(reassign=False, on_event=on_event)
                return ok

            _emit("Re-seating all seats...")
            self.engine.reset_all_to_core()
            displays_now, total_now = self.dm.get_displays()
            cfg_now = self.cfg_mgr.auto_generate_config(self.dm, force_reassign=False)
            seats_now = cfg_now.get("seats", [])
            seats_now, _ = self.dm.assign_displays_to_seats(seats_now, displays_now)
            cfg_now["seats"] = seats_now
            self.cfg_mgr.save_config(cfg_now)
            self.engine.apply_seating(seats_now, displays_now, (total_now["width"], total_now["height"]))
            if self.reseat_key:
                self.reseat_key.update_seat_keyboards(self.engine.seat_keyboards)
            for sid, win in list(self.kiosk.window_ids.items()):
                self.engine.focus_seat_keyboard(sid, win)
            _emit("Seats re-assigned.")
            return True
        except Exception as exc:
            logger.exception("Re-seat failed")
            _emit(f"Re-seat failed: {exc}")
            return False


# Global singleton instance
session_mgr = SessionManager()
''')

# ---------------------------------------------------------------------------
# 2. gui/app.py
# ---------------------------------------------------------------------------
app_py = (ROOT / "gui/app.py").read_text()

app_py = app_py.replace(
'''from core import overlay_state
from core.device_manager import DeviceManager
from core.xinput_seating import XInputSeatingEngine
from core.kiosk_manager import KioskManager
from core.config_manager import ConfigManager''',
'''from core import overlay_state
from core.session_manager import session_mgr'''
)

app_py = app_py.replace(
'''    def __init__(self, overlay_mode: bool = False, target_seat: str = "seat1"):
        super().__init__(title="XInput Pseudo-Seating Control Panel")
        self.set_default_size(960, 680)
        self.set_position(Gtk.WindowPosition.CENTER)

        # ── Overlay State ──
        self.is_overlay_active: bool = False
        self.active_overlay_seat: Optional[str] = None
        self._overlay_mode_standalone: bool = overlay_mode
        self._overlay_hb_id = None
        self._overlay_xid = None
        self._hotplug_timer = None
        self._hotplug_busy = False

        # ── Initialize Core Engines ──
        self.device_mgr = DeviceManager()
        self.seating_engine = XInputSeatingEngine()
        self.kiosk_mgr = KioskManager()
        self.config_mgr = ConfigManager()''',
'''    def __init__(self, overlay_mode: bool = False, target_seat: str = "seat1", auto_start_session: bool = True):
        super().__init__(title="XInput Pseudo-Seating Control Panel")
        self.set_default_size(960, 680)
        self.set_position(Gtk.WindowPosition.CENTER)

        # ── Overlay State ──
        self.is_overlay_active: bool = False
        self.active_overlay_seat: Optional[str] = None
        self._overlay_mode_standalone: bool = overlay_mode
        self._auto_start_session: bool = auto_start_session
        self._shutting_down: bool = False
        self._overlay_hb_id = None
        self._overlay_xid = None
        self._hotplug_timer = None
        self._hotplug_busy = False

        # ── Share Unified SessionManager Core Engines ──
        self.device_mgr = session_mgr.dm
        self.seating_engine = session_mgr.engine
        self.kiosk_mgr = session_mgr.kiosk
        self.config_mgr = session_mgr.cfg_mgr'''
)

app_py = app_py.replace(
'''        # First run check
        if not self.config_mgr.config_path.exists():
            GLib.idle_add(self._show_setup_wizard)

        # If launched directly in overlay mode
        if overlay_mode:
            GLib.idle_add(self.show_overlay, target_seat)''',
'''        # Bind GUI startup with Desktop/Kiosk session startup
        if overlay_mode:
            if not session_mgr.is_session_active():
                GLib.idle_add(self._auto_start_kiosks_on_launch, True, target_seat)
            else:
                GLib.idle_add(self.show_overlay, target_seat)
        elif self._auto_start_session:
            GLib.idle_add(self._auto_start_kiosks_on_launch, False, target_seat)

    def _auto_start_kiosks_on_launch(self, show_overlay_after: bool = False, target_seat: str = "seat1"):
        """Starts the desktop seating & kiosk session automatically when the GUI starts if not already active."""
        if not session_mgr.is_session_active():
            self.dashboard_tab.append_log("Auto-starting seating and dual Firefox kiosks with GUI startup...")
            def _worker():
                ok, msg = session_mgr.start_session(on_event=lambda m: self.dashboard_tab.append_log(m))
                if ok:
                    GLib.idle_add(self.hotkey_mgr.update_seat_keyboards, self.seating_engine.seat_keyboards)
                    GLib.idle_add(self.show_info, "Pseudo-seating active! Dual Firefox kiosks running.", 0)
                    if show_overlay_after:
                        GLib.idle_add(self.show_overlay, target_seat)
                    else:
                        GLib.idle_add(self.iconify)
                else:
                    GLib.idle_add(self.show_info, msg, 1)
                GLib.idle_add(self.refresh_all)
            threading.Thread(target=_worker, daemon=True).start()
        else:
            self._ensure_masters()
            if self.seating_engine.seat_keyboards:
                self.hotkey_mgr.update_seat_keyboards(self.seating_engine.seat_keyboards)
            self.refresh_all()
        return False'''
)

app_py = app_py.replace(
'''    def _on_ipc_stop(self):
        logger.info("Received IPC STOP/QUIT command. Closing Control Board...")''',
'''    def _on_ipc_stop(self):
        self._shutting_down = True
        logger.info("Received IPC STOP/QUIT command. Closing Control Board...")'''
)

app_py = app_py.replace(
'''    def _stop_and_exit_app(self):
        """Unified shutdown: stops supervisor, kiosks, devices, and closes GUI."""
        from core.session_manager import session_mgr
        logger.info("Executing unified shutdown from Control Board...")
        session_mgr.stop_session(exit_gui=True)''',
'''    def _stop_and_exit_app(self):
        """Unified shutdown: stops supervisor, kiosks, devices, and closes GUI."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Executing unified shutdown from Control Board...")
        session_mgr.stop_session(exit_gui=True)'''
)

app_py = app_py.replace(
'''    def _on_window_destroy(self, _widget):
        logger.info("Window closing. Stopping kiosk watchdog and overlay services...")
        overlay_state.clear()  # never leave a seat whitelisted after the GUI exits
        if hasattr(self, 'hotkey_mgr') and self.hotkey_mgr:
            self.hotkey_mgr.stop()
        if hasattr(self, 'ipc_server') and self.ipc_server:
            self.ipc_server.stop()
        if hasattr(self, 'display_guard') and self.display_guard:
            self.display_guard.stop()
            self.display_guard = None
        self.kiosk_mgr.stop_watchdog()
        if Gtk.main_level() > 0:
            Gtk.main_quit()''',
'''    def _on_window_destroy(self, _widget):
        logger.info("Window closing. Binding GUI exit with full kiosk & desktop session teardown...")
        overlay_state.clear()  # never leave a seat whitelisted after the GUI exits
        if hasattr(self, 'hotkey_mgr') and self.hotkey_mgr:
            self.hotkey_mgr.stop()
        if hasattr(self, 'ipc_server') and self.ipc_server:
            self.ipc_server.stop()
        if not self._shutting_down:
            self._shutting_down = True
            session_mgr.stop_session(exit_gui=False)
        if Gtk.main_level() > 0:
            Gtk.main_quit()'''
)

(ROOT / "gui/app.py").write_text(app_py)

# ---------------------------------------------------------------------------
# 3. gui/tabs/dashboard_tab.py
# ---------------------------------------------------------------------------
dash_py = (ROOT / "gui/tabs/dashboard_tab.py").read_text()

dash_py = dash_py.replace(
'''            ok, msg = session_mgr.start_session(on_event=lambda m: self.append_log(m))
            if ok:
                self.append_log("Dual Firefox kiosks launched successfully.")''',
'''            ok, msg = session_mgr.start_session(on_event=lambda m: self.append_log(m))
            if ok:
                GLib.idle_add(self.app.hotkey_mgr.update_seat_keyboards, self.app.seating_engine.seat_keyboards)
                self.append_log("Dual Firefox kiosks launched successfully.")'''
)

dash_py = dash_py.replace(
'''        def _worker():
            from core.session_manager import session_mgr
            session_mgr.stop_session(exit_gui=False, on_event=lambda m: self.append_log(m))
            GLib.idle_add(self.app.show_info, "All seats stopped. System restored to single-seat core.", 0)
            GLib.idle_add(self.refresh_metrics)
        threading.Thread(target=_worker, daemon=True).start()''',
'''        def _worker():
            from core.session_manager import session_mgr
            session_mgr.stop_session(exit_gui=True, on_event=lambda m: self.append_log(m))
            GLib.idle_add(self.app._on_ipc_stop)
        threading.Thread(target=_worker, daemon=True).start()'''
)

(ROOT / "gui/tabs/dashboard_tab.py").write_text(dash_py)

# ---------------------------------------------------------------------------
# 4. launcher.py
# ---------------------------------------------------------------------------
(ROOT / "launcher.py").write_text('''#!/usr/bin/env python3
"""
One-click Kiosk Launcher (use from a desktop / menu shortcut instead of autostart).

    launch.sh                 detect hardware -> assign seats -> save config -> launch kiosks & GUI
    launch.sh --reassign      forget saved seat assignment and detect from scratch (restarts)
    launch.sh --restart       stop a running session first, then launch again
    launch.sh --no-detect     launch with the saved config only
    launch.sh --reseat        re-seat all devices of a running session (same as Ctrl+Shift+M+R)
    launch.sh --reseat --hard ... and restart the browsers too
    launch.sh --stop          stop kiosks, close GUI, reset input devices to Virtual core
    launch.sh --control-board show the Control Board (also what a second click on the shortcut does)

The process stays alive as the supervisor (watchdog, shield, hotplug handling, hotkey)
and binds its lifecycle with the Control Board GUI.
"""

import argparse
import fcntl
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

CONFIG_DIR = Path.home() / ".config" / "xinput-seats"
LOCK_FILE = CONFIG_DIR / "launcher.lock"
PID_FILE = CONFIG_DIR / "launcher.pid"
LOG_FILE = CONFIG_DIR / "launcher.log"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("launcher")

REQUIRED = ["xinput", "xrandr", "firefox", "wmctrl", "xdotool"]


def notify(msg: str, urgent: bool = False):
    log.info(msg)
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Kiosk Launcher", "-u", "critical" if urgent else "normal",
                          "-t", "6000", "XInput Kiosk", msg],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid():
    try:
        pid = int(PID_FILE.read_text().strip())
        return pid if _pid_alive(pid) else None
    except (OSError, ValueError):
        return None


def spawn_control_board(seat: str, start_hidden: bool = False):
    env = os.environ.copy()
    env.pop("WAYLAND_DISPLAY", None)
    cmd = [sys.executable, str(PROJECT_DIR / "main.py"), "--seat", seat]
    if not start_hidden:
        cmd.append("--overlay")
    return subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def cmd_control_board(seat: str) -> int:
    from core.hotkey_manager import send_overlay_command
    if not send_overlay_command(f"SHOW {seat}"):
        spawn_control_board(seat)
    return 0


def emergency_cleanup():
    """Kills leftover kiosk browsers and returns every input device to Virtual core."""
    from core.session_manager import session_mgr
    session_mgr.stop_session()


def cmd_stop() -> int:
    from core.session_manager import session_mgr
    session_mgr.stop_session(exit_gui=True, on_event=notify)
    notify("Kiosks stopped, input devices returned to the normal desktop.")
    return 0


def cmd_reseat(hard: bool) -> int:
    pid = _read_pid()
    if not pid:
        notify("No running kiosk session to re-seat. Start it with launch.sh.", urgent=True)
        return 1
    os.kill(pid, signal.SIGUSR2 if hard else signal.SIGUSR1)
    return 0


def acquire_lock():
    fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        fd.close()
        return None


def launch(args) -> int:
    missing = [c for c in REQUIRED if not shutil.which(c)]
    if missing:
        notify(f"Missing tools: {', '.join(missing)}. Install them and try again.", urgent=True)
        return 1

    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"

    if args.restart or args.reassign:
        if _read_pid():
            cmd_stop()
            time.sleep(1)

    lock = acquire_lock()
    if lock is None:
        notify("Kiosks are already running - opening the Control Board.")
        return cmd_control_board(args.seat)

    from core.session_manager import session_mgr
    from core.hotkey_manager import HotkeyManager, send_overlay_command

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())

    signal.signal(signal.SIGUSR1, lambda *_: session_mgr.reseat_session(hard=False, on_event=notify))
    signal.signal(signal.SIGUSR2, lambda *_: session_mgr.reseat_session(hard=True, on_event=notify))

    gui_proc = None
    try:
        ok, msg = session_mgr.start_session(
            reassign=args.reassign,
            no_detect=args.no_detect,
            on_event=notify,
        )
        if not ok:
            notify(msg, urgent=True)
            return 1

        # Bind GUI startup: spawn the Control Board GUI alongside the desktop session
        if not send_overlay_command("NOP"):
            gui_proc = spawn_control_board(args.seat, start_hidden=True)

        notify("Kiosks running. Ctrl+Shift+M+S opens the Control Board." if ok
               else "A kiosk failed to start - see ~/.config/xinput-seats/launcher.log", urgent=not ok)

        gui_until = [0.0]
        def on_hotkey(seat_id, _master, _slave):
            if send_overlay_command("NOP"):
                return
            if time.time() > gui_until[0]:
                gui_until[0] = time.time() + 8
                spawn_control_board(seat_id)

        session_mgr.hotkey_mgr = HotkeyManager(on_trigger=on_hotkey, seat_keyboards=session_mgr.engine.seat_keyboards)
        session_mgr.hotkey_mgr.start()

        last_hotplug = 0.0
        while not stop.wait(0.4):
            if gui_proc is not None and gui_proc.poll() is not None:
                log.info("Bound Control Board GUI exited; stopping desktop session.")
                break
            if time.time() - last_hotplug >= 1.5:
                last_hotplug = time.time()
                for msg in session_mgr.engine.check_and_reseat_hotplugged_devices(
                    session_mgr.cfg_mgr.load_config().get("seats", [])
                ):
                    notify(msg)
        return 0
    except Exception as exc:
        log.exception("Launcher failed")
        notify(f"Launcher failed: {exc}", urgent=True)
        return 1
    finally:
        log.info("Shutting down launcher session...")
        session_mgr.stop_session(exit_gui=True, on_event=log.info)


def main():
    ap = argparse.ArgumentParser(description="One-click kiosk launcher")
    ap.add_argument("--reassign", action="store_true", help="re-detect hardware from scratch (implies --restart)")
    ap.add_argument("--restart", action="store_true", help="stop a running session first")
    ap.add_argument("--no-detect", action="store_true", help="use the saved config without detection")
    ap.add_argument("--reseat", action="store_true", help="re-seat a running session (Ctrl+Shift+M+R)")
    ap.add_argument("--hard", action="store_true", help="with --reseat: restart the browsers too")
    ap.add_argument("--stop", action="store_true", help="stop kiosks and reset devices")
    ap.add_argument("--control-board", action="store_true", help="show the Control Board")
    ap.add_argument("--seat", default="seat1", help="seat for --control-board (default seat1)")
    args = ap.parse_args()

    if args.stop:
        sys.exit(cmd_stop())
    if args.reseat:
        sys.exit(cmd_reseat(args.hard))
    if args.control_board:
        sys.exit(cmd_control_board(args.seat))
    sys.exit(launch(args))


if __name__ == "__main__":
    main()
''')

# ---------------------------------------------------------------------------
# 5. main.py
# ---------------------------------------------------------------------------
main_py = (ROOT / "main.py").read_text()

main_py = main_py.replace(
'''    dev_mgr = DeviceManager()
    engine = XInputSeatingEngine()
    kiosk_mgr = KioskManager()
    cfg_mgr = ConfigManager()
    kiosk_mgr.on_window_ready = engine.focus_seat_keyboard''',
'''    from core.session_manager import session_mgr
    dev_mgr, engine, kiosk_mgr, cfg_mgr = session_mgr.dm, session_mgr.engine, session_mgr.kiosk, session_mgr.cfg_mgr'''
)

main_py = main_py.replace(
'''    if args.stop:
        print("[*] Stopping all Firefox kiosks...")
        kiosk_mgr.stop_all_kiosks()
        print("[*] Resetting all XInput devices to Virtual core...")
        engine.reset_all_to_core()
        print("[✓] Pseudo-seating stopped and restored to single-seat core.")
        return''',
'''    if args.stop:
        print("[*] Stopping session, GUI, Firefox kiosks, and resetting XInput devices...")
        session_mgr.stop_session(exit_gui=True, on_event=lambda m: print(f"[*] {m}"))
        print("[✓] Pseudo-seating stopped and restored to single-seat core.")
        return'''
)

old_start_block = main_py[main_py.index("    if args.start:"):main_py.index("def main():")]
new_start_block = '''    if args.start:
        ok, msg = session_mgr.start_session(on_event=lambda m: print(f"[*] {m}"))
        if ok:
            print("[✓] Dual Firefox kiosks launched successfully!")
            from core.hotkey_manager import HotkeyManager
            session_mgr.hotkey_mgr = HotkeyManager(launch_gui_if_needed=True, seat_keyboards=engine.seat_keyboards)
            session_mgr.hotkey_mgr.start()
            print("    ⌨ Super Shortcut: Press Ctrl+Shift+M+S on any seat to overlay the control board.")
            print("    Press Ctrl+C to stop kiosks and reset seating.")
            try:
                import time
                while True:
                    time.sleep(2)
                    seats = cfg_mgr.load_config().get("seats", [])
                    reseated = engine.check_and_reseat_hotplugged_devices(seats)
                    if reseated:
                        for m in reseated:
                            print(f"[✓] Hotplug: {m}")
            except KeyboardInterrupt:
                print("\\n[*] Stopping kiosks, display guard, hotkey listener and resetting devices...")
                session_mgr.stop_session(exit_gui=True, on_event=lambda m: print(f"[*] {m}"))
                print("[✓] Cleanup complete.")
        else:
            print(f"[✗] {msg}")
        return


'''
main_py = main_py.replace(old_start_block, new_start_block)
(ROOT / "main.py").write_text(main_py)

# ---------------------------------------------------------------------------
# 6. core/autostart.py
# ---------------------------------------------------------------------------
autostart_py = (ROOT / "core/autostart.py").read_text()
old_fn = autostart_py[autostart_py.index("def run_startup_reseat_and_launch"):]
new_fn = '''def run_startup_reseat_and_launch(wait_timeout_sec: int = 20) -> bool:
    """
    Executed on system boot / power recovery.
    Delegates to the unified SessionManager and binds the GUI & desktop lifecycle.
    """
    from core.session_manager import session_mgr
    from core.hotkey_manager import HotkeyManager

    logger.info("Starting automated power outage recovery / boot reseating sequence...")

    start_time = time.time()
    while time.time() - start_time < wait_timeout_sec:
        active_disp, _ = session_mgr.dm.probe_and_activate_displays()
        if active_disp:
            break
        time.sleep(1)

    ok, msg = session_mgr.start_session(on_event=logger.info)
    if ok:
        logger.info("Dual kiosks launched successfully on startup!")
        session_mgr.hotkey_mgr = HotkeyManager(launch_gui_if_needed=True, seat_keyboards=session_mgr.engine.seat_keyboards)
        session_mgr.hotkey_mgr.start()
        logger.info("Super Keyboard Shortcut (Ctrl+Shift+M+S) listener active for kiosk overlay.")
        try:
            while True:
                time.sleep(2)
                seats = session_mgr.cfg_mgr.load_config().get("seats", [])
                reseated = session_mgr.engine.check_and_reseat_hotplugged_devices(seats)
                if reseated:
                    for m in reseated:
                        logger.info(f"[Autostart Hotplug Reseat] {m}")
        except (KeyboardInterrupt, SystemExit):
            logger.info("Stopping kiosks, display guard, hotkey listener and resetting seating...")
            session_mgr.stop_session(exit_gui=True, on_event=logger.info)
    else:
        logger.error(f"Failed to launch kiosks on startup: {msg}")
    return ok
'''
(ROOT / "core/autostart.py").write_text(autostart_py.replace(old_fn, new_fn))

# ---------------------------------------------------------------------------
# 7. xinput-multiseat.desktop & install-launcher.sh
# ---------------------------------------------------------------------------
desktop_file = (ROOT / "xinput-multiseat.desktop").read_text()
desktop_file = desktop_file.replace("Exec=/home/aelaf/Projects/xinput/start.sh", "Exec=@APP_DIR@/start.sh")
(ROOT / "xinput-multiseat.desktop").write_text(desktop_file)

print("[✓] All files patched successfully! Run `git diff` to inspect the changes.")