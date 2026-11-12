#!/usr/bin/env python3
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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from core.paths import CONFIG_DIR, PID_FILE, LOCK_FILE, LOG_FILE, PROFILES_DIR

logger = logging.getLogger("xinput_seating.session")


@dataclass
class StartResult:
    ok: bool
    code: str
    message: str

    OK = "OK"
    NEED_DISPLAYS = "NEED_DISPLAYS"
    NEED_DEVICES = "NEED_DEVICES"
    KIOSK_FAILED = "KIOSK_FAILED"
    ALREADY_RUNNING = "ALREADY_RUNNING"

    def __iter__(self) -> Iterator:
        yield self.ok
        yield self.message


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
        try:
            self.kiosk.stop_shield()
        except Exception:
            pass
        try:
            from core.kiosk_shield import restore_all_keymaps
            restore_all_keymaps()
        except Exception:
            pass

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
            prof_dir = str(PROFILES_DIR)
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
        allow_partial: Optional[bool] = None,
    ) -> StartResult:
        """
        Unified start-up wrapper.
        Validates displays, assigns seats respecting preference & priority rules,
        applies seating, launches dual kiosks, and starts supervisors.
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
            disp_names = ", ".join(d.get("name", "Unknown") for d in active_displays) or "none"
            msg = f"Only {len(active_displays)} display(s) detected: {disp_names}"
            _emit(f"Fewer than 2 displays detected ({disp_names}). Dual-seat kiosk requires 2 monitors.")
            return StartResult(ok=False, code=StartResult.NEED_DISPLAYS, message=msg)

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
        seats = resolved
        self.cfg_mgr.save_config(cfg)

        for (sid, cls), dev in sorted(assignments.items()):
            seat = next((x for x in cfg["seats"] if x.get("id") == sid), {})
            _emit(f"  {sid} {cls}: " + (f"{dev['name']} (ID {dev['id']})" if dev else "not connected")
                  + f"   display={seat.get('display')}")

        # Check for missing devices if partial seating is not allowed
        missing_devs = []
        for s in resolved:
            sid = s.get("id", "seat")
            if not s.get("mouse_id"):
                missing_devs.append(f"{sid} mouse")
            if not s.get("keyboard_id"):
                missing_devs.append(f"{sid} keyboard")

        effective_allow_partial = allow_partial
        if effective_allow_partial is None:
            effective_allow_partial = cfg.get("settings", {}).get("allow_partial_seats", False)

        if missing_devs and not effective_allow_partial:
            msg = f"Missing input device(s): {', '.join(missing_devs)}"
            _emit(f"{msg}. Dual-seat kiosk requires dedicated mouse and keyboard for each seat.")
            return StartResult(ok=False, code=StartResult.NEED_DEVICES, message=msg)

        # 3. Apply XInput pseudo-seating (MPX masters + confinement)
        _emit("Applying XInput MPX master pairs and device reattachments...")
        settings = cfg.get("settings", {})
        confine = settings.get("confine_mouse_to_screen", True)
        results = self.engine.apply_seating(
            seats, displays, (total["width"], total["height"]),
            confine_mouse_to_screen=confine,
        )
        _emit(f"Seating applied: {results}")

        # 4. Launch dual Firefox kiosks
        _emit("Launching dual Firefox kiosks with isolated profiles...")
        s1_url = seats[0].get("kiosk_url") if len(seats) > 0 else "https://duckduckgo.com"
        s2_url = seats[1].get("kiosk_url") if len(seats) > 1 else "https://www.google.com"
        watchdog_interval = float(settings.get("watchdog_interval_sec", 1.5))

        ok = self.kiosk.launch_dual_kiosks(
            seat1_url=s1_url or "https://duckduckgo.com",
            seat2_url=s2_url or "https://www.google.com",
            displays_info=displays,
            enforce_lock=True,
            seat_configs=seats,
            watchdog_interval_sec=watchdog_interval,
        )

        if ok:
            PID_FILE.write_text(str(os.getpid()))
            self._start_runtime_services(on_event=on_event)
            _emit("Dual Firefox kiosks launched successfully!")
            return StartResult(ok=True, code=StartResult.OK, message="Dual Firefox kiosks running.")
        else:
            _emit("Failed to launch one or more Firefox kiosks.")
            return StartResult(ok=False, code=StartResult.KIOSK_FAILED, message="Failed to launch Firefox kiosks.")

    def start_session_legacy(
        self,
        reassign: bool = False,
        no_detect: bool = False,
        on_event: Optional[Callable[[str], None]] = None,
        allow_partial: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        res = self.start_session(reassign=reassign, no_detect=no_detect, on_event=on_event, allow_partial=allow_partial)
        return res.ok, res.message

    def restart_session(
        self,
        reassign: bool = False,
        on_event: Optional[Callable[[str], None]] = None,
        allow_partial: Optional[bool] = None,
    ) -> StartResult:
        """Stops any running session (without exiting the GUI) and starts a fresh session."""
        self.stop_session(exit_gui=False, on_event=on_event)
        time.sleep(0.5)
        return self.start_session(reassign=reassign, no_detect=False, on_event=on_event, allow_partial=allow_partial)

    def reseat_session(
        self,
        hard: bool = False,
        on_event: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Re-seats all devices of a running session.
        Soft (hard=False): keeps kiosks running and hotplug-reseats devices without destroying masters.
        Hard (hard=True): restarts kiosks and resets all seating from scratch.
        """
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
                _emit("Hard reseat requested or kiosks dead: restarting session...")
                res = self.restart_session(reassign=False, on_event=on_event)
                return res.ok

            _emit("Soft re-seating devices (keeping browser kiosks running)...")
            self.engine.force_recheck()
            cfg_now = self.cfg_mgr.load_config()
            seats_now = cfg_now.get("seats", [])
            displays_now, total_now = self.dm.get_displays()
            total_geom = (total_now["width"], total_now["height"]) if total_now else None

            msgs = self.engine.check_and_reseat_hotplugged_devices(
                seats_now,
                displays_info=displays_now,
                total_screen=total_geom,
            )
            for m in msgs:
                _emit(f"Reseat: {m}")

            # Re-focus running kiosks to their seat keyboards
            for sid, win in list(self.kiosk.window_ids.items()):
                self.engine.focus_seat_keyboard(sid, win)

            _emit("Soft re-seat complete.")
            return True
        except Exception as exc:
            logger.exception("Re-seat failed")
            _emit(f"Re-seat failed: {exc}")
            return False


# Global singleton instance
session_mgr = SessionManager()
