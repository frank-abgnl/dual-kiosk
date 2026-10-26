#!/usr/bin/env python3
"""
Firefox Kiosk & Profile Manager.
Launches separate Firefox instances with dedicated profiles onto specific screens.
Enforces non-minimizable, non-closable kiosk mode with an automated watchdog supervisor.
"""

import subprocess
import os
import time
import threading
import logging
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("xinput_seating.kiosk")

CONFIG_DIR = Path.home() / ".config" / "xinput-seats"
PROFILES_DIR = CONFIG_DIR / "profiles"


def run_command(args, timeout=5):
    """Run a shell command safely, returning (stdout, stderr, returncode)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.stdout.strip(), proc.stderr.strip(), proc.returncode
    except Exception as exc:
        return "", str(exc), -1


class KioskManager:
    """Manages multi-screen Firefox kiosk sessions and enforces window restrictions."""

    def __init__(self):
        self.running_processes: Dict[str, subprocess.Popen] = {}
        self.window_ids: Dict[str, str] = {}
        self.kiosk_configs: Dict[str, Dict] = {}
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_watchdog = threading.Event()
        self._lock = threading.Lock()

        # Ensure profile base directories exist
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Profile Setup & Lockdown
    # -----------------------------------------------------------------------
    def get_profile_path(self, seat_id: str) -> Path:
        """Returns the profile directory path for a given seat."""
        p = PROFILES_DIR / seat_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def setup_profile(self, seat_id: str, kiosk_url: str = "https://duckduckgo.com") -> Path:
        """
        Creates or updates a locked-down Firefox profile for the seat.
        Configures user.js and distribution policies to restrict navigation and close actions.
        """
        profile_path = self.get_profile_path(seat_id)

        # 1. Write user.js preferences
        user_js = profile_path / "user.js"
        prefs = f"""// Auto-generated kiosk preferences for seat: {seat_id}
user_pref("browser.startup.homepage", "{kiosk_url}");
user_pref("browser.startup.page", 1);
user_pref("browser.tabs.closeWindowWithLastTab", false);
user_pref("browser.warnOnQuit", false);
user_pref("browser.fullscreen.autohide", false);
user_pref("browser.fullscreen.animate", false);
user_pref("toolkit.cosmeticAnimations.enabled", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.sessionstore.max_resumed_crashes", 0);
user_pref("dom.disable_window_open_feature.close", true);
user_pref("dom.disable_window_open_feature.minimizable", true);
user_pref("browser.link.open_newwindow", 1);
user_pref("browser.link.open_newwindow.restriction", 0);
user_pref("extensions.pocket.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("privacy.sanitize.sanitizeOnShutdown", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("app.update.auto", false);
user_pref("app.update.enabled", false);
"""
        try:
            user_js.write_text(prefs)
        except Exception as exc:
            logger.warning(f"Could not write user.js for {seat_id}: {exc}")

        # 2. Write distribution/policies.json inside the profile
        dist_dir = profile_path / "distribution"
        dist_dir.mkdir(parents=True, exist_ok=True)
        policies_json = dist_dir / "policies.json"
        policies_data = {
            "policies": {
                "DisableAppUpdate": True,
                "DisableDeveloperTools": True,
                "PromptForDownloadLocation": False,
                "OverrideFirstRunPage": kiosk_url,
                "OverridePostUpdatePage": kiosk_url,
                "SanitizeOnShutdown": False,
                "DontCheckDefaultBrowser": True,
            }
        }
        try:
            policies_json.write_text(json.dumps(policies_data, indent=2))
        except Exception as exc:
            logger.warning(f"Could not write policies.json for {seat_id}: {exc}")

        return profile_path

    # -----------------------------------------------------------------------
    # Firefox Launching
    # -----------------------------------------------------------------------
    def launch_kiosk(
        self,
        seat_id: str,
        kiosk_url: str = "https://duckduckgo.com",
        monitor_index: int = 0,
        display_geometry: Optional[Tuple[int, int, int, int]] = None,
        enforce_lock: bool = True,
    ) -> bool:
        """
        Launches a single Firefox instance in kiosk mode with its dedicated profile.
        """
        with self._lock:
            # Stop existing instance if running
            if seat_id in self.running_processes:
                self._terminate_seat_process(seat_id)

            profile_path = self.setup_profile(seat_id, kiosk_url)

            # Build command:
            # Firefox flags: --new-instance --profile <path> --kiosk [--kiosk-monitor <idx>] <url>
            cmd = [
                "firefox",
                "--new-instance",
                "--profile", str(profile_path),
                "--kiosk",
                "--kiosk-monitor", str(monitor_index),
                kiosk_url,
            ]

            logger.info(f"Launching Firefox Kiosk for '{seat_id}' (monitor {monitor_index}): {' '.join(cmd)}")
            try:
                env = os.environ.copy()
                env.pop("WAYLAND_DISPLAY", None)  # Ensure X11 / Xwayland backend for xinput compatibility
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                )
                self.running_processes[seat_id] = proc
                self.kiosk_configs[seat_id] = {
                    "kiosk_url": kiosk_url,
                    "monitor_index": monitor_index,
                    "display_geometry": display_geometry,
                    "enforce_lock": enforce_lock,
                }
            except Exception as exc:
                logger.error(f"Failed to spawn Firefox for seat '{seat_id}': {exc}")
                return False

        # Apply window enforcement in background thread once window appears
        if enforce_lock:
            t = threading.Thread(
                target=self._enforce_window_after_launch,
                args=(seat_id, proc.pid, display_geometry),
                daemon=True,
            )
            t.start()

        return True

    def _terminate_seat_process(self, seat_id: str):
        """Terminates an individual seat process."""
        proc = self.running_processes.get(seat_id)
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self.running_processes.pop(seat_id, None)
            self.window_ids.pop(seat_id, None)

    # -----------------------------------------------------------------------
    # Window Constraints Enforcement (Non-Minimizable, Non-Closable, Fullscreen)
    # -----------------------------------------------------------------------
    def _find_window_id_for_pid(self, pid: int, timeout_sec: int = 6) -> Optional[str]:
        """Finds the X11 window ID associated with the process PID."""
        start = time.time()
        while time.time() - start < timeout_sec:
            # Try xdotool
            out, _, rc = run_command(["xdotool", "search", "--pid", str(pid)])
            if rc == 0 and out:
                # Return the last window id (typically the top-level window)
                lines = [l.strip() for l in out.splitlines() if l.strip()]
                if lines:
                    return lines[-1]

            # Try wmctrl
            out2, _, rc2 = run_command(["wmctrl", "-l", "-p"])
            if rc2 == 0 and out2:
                for line in out2.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == str(pid):
                        return parts[0]

            time.sleep(0.4)
        return None

    def _enforce_window_after_launch(
        self,
        seat_id: str,
        pid: int,
        display_geometry: Optional[Tuple[int, int, int, int]] = None,
    ):
        """Polls for window mapping and applies EWMH lock flags."""
        win_id = self._find_window_id_for_pid(pid)
        if not win_id:
            logger.warning(f"Could not find window ID for seat '{seat_id}' (PID {pid}) within timeout.")
            return

        with self._lock:
            self.window_ids[seat_id] = win_id

        logger.info(f"Applying kiosk lock constraints to window {win_id} for seat '{seat_id}'...")
        self.lock_window(win_id, display_geometry)

    def lock_window(self, win_id: str, display_geometry: Optional[Tuple[int, int, int, int]] = None):
        """
        Enforces non-minimizable, non-closable, fullscreen, and stays-on-top on an X11 window.
        """
        # 1. If geometry is provided, move and size window to exact monitor bounds
        if display_geometry:
            w, h, x, y = display_geometry
            # Remove fullscreen briefly to move window
            run_command(["wmctrl", "-i", "-r", win_id, "-b", "remove,fullscreen"])
            run_command(["wmctrl", "-i", "-r", win_id, "-e", f"0,{x},{y},{w},{h}"])

        # 2. Add Fullscreen and Always on Top
        run_command(["wmctrl", "-i", "-r", win_id, "-b", "add,fullscreen,above"])

        # 3. Strip Close and Minimize permissions via _NET_WM_ALLOWED_ACTIONS
        # Setting ALLOWED_ACTIONS exclusively to FULLSCREEN prevents WM close/minimize buttons
        run_command(["xprop", "-id", win_id, "-set", "_NET_WM_ALLOWED_ACTIONS", "_NET_WM_ACTION_FULLSCREEN"])

        # 4. Enforce _NET_WM_STATE
        run_command([
            "xprop", "-id", win_id, "-f", "_NET_WM_STATE", "32a",
            "-set", "_NET_WM_STATE",
            "_NET_WM_STATE_FULLSCREEN, _NET_WM_STATE_ABOVE, _NET_WM_STATE_STAYS_ON_TOP"
        ])

    def restore_and_unminimize(self, win_id: str):
        """Restores a minimized or hidden kiosk window back to fullscreen."""
        logger.info(f"Unminimizing and raising kiosk window {win_id}...")
        run_command(["wmctrl", "-i", "-a", win_id])
        run_command(["wmctrl", "-i", "-r", win_id, "-b", "remove,hidden"])
        run_command(["wmctrl", "-i", "-r", win_id, "-b", "add,fullscreen,above"])

    # -----------------------------------------------------------------------
    # Supervisor Watchdog
    # -----------------------------------------------------------------------
    def start_watchdog(self, interval_sec: float = 1.5):
        """Starts the background supervisor watchdog thread."""
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return

        self._stop_watchdog.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            args=(interval_sec,),
            daemon=True,
            name="KioskWatchdog",
        )
        self._watchdog_thread.start()
        logger.info("Kiosk supervisor watchdog started.")

    def stop_watchdog(self):
        """Stops the supervisor watchdog."""
        self._stop_watchdog.set()
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=2)
            self._watchdog_thread = None
        logger.info("Kiosk supervisor watchdog stopped.")

    def _watchdog_loop(self, interval_sec: float):
        """Main monitoring loop: respawns killed instances and unminimizes hidden ones."""
        while not self._stop_watchdog.is_set():
            time.sleep(interval_sec)

            with self._lock:
                active_seats = list(self.kiosk_configs.keys())

            for seat_id in active_seats:
                if self._stop_watchdog.is_set():
                    break

                with self._lock:
                    proc = self.running_processes.get(seat_id)
                    cfg = self.kiosk_configs.get(seat_id, {})
                    win_id = self.window_ids.get(seat_id)

                # Check 1: Did process exit?
                if proc is None or proc.poll() is not None:
                    if cfg.get("enforce_lock", True):
                        logger.warning(f"[Watchdog] Kiosk process for seat '{seat_id}' terminated! Auto-respawning...")
                        self.launch_kiosk(
                            seat_id=seat_id,
                            kiosk_url=cfg.get("kiosk_url", "https://duckduckgo.com"),
                            monitor_index=cfg.get("monitor_index", 0),
                            display_geometry=cfg.get("display_geometry"),
                            enforce_lock=True,
                        )
                    continue

                # Check 2: Was window minimized / hidden?
                if win_id and cfg.get("enforce_lock", True):
                    out, _, rc = run_command(["xprop", "-id", win_id, "_NET_WM_STATE"])
                    if rc == 0 and "_NET_WM_STATE_HIDDEN" in out:
                        logger.warning(f"[Watchdog] Seat '{seat_id}' window was minimized! Restoring immediately...")
                        self.restore_and_unminimize(win_id)

    # -----------------------------------------------------------------------
    # Multi-Seat Launch Orchestrator
    # -----------------------------------------------------------------------
    def launch_dual_kiosks(
        self,
        seat1_url: str = "https://duckduckgo.com",
        seat2_url: str = "https://www.google.com",
        displays_info: Optional[List[Dict]] = None,
        enforce_lock: bool = True,
    ) -> bool:
        """
        Launches Seat 1 on Monitor 0 and Seat 2 on Monitor 1 in full kiosk mode.
        """
        geom1 = None
        geom2 = None
        if displays_info and len(displays_info) >= 1:
            d0 = displays_info[0]
            geom1 = (d0["width"], d0["height"], d0["x"], d0["y"])
        if displays_info and len(displays_info) >= 2:
            d1 = displays_info[1]
            geom2 = (d1["width"], d1["height"], d1["x"], d1["y"])

        ok1 = self.launch_kiosk("seat1", seat1_url, monitor_index=0, display_geometry=geom1, enforce_lock=enforce_lock)
        ok2 = self.launch_kiosk("seat2", seat2_url, monitor_index=1, display_geometry=geom2, enforce_lock=enforce_lock)

        if enforce_lock:
            self.start_watchdog()

        return ok1 and ok2

    def stop_all_kiosks(self):
        """Stops all running kiosk instances and shuts down the watchdog."""
        self.stop_watchdog()
        with self._lock:
            seats = list(self.running_processes.keys())
            for seat_id in seats:
                self._terminate_seat_process(seat_id)
            self.kiosk_configs.clear()
            self.window_ids.clear()
        logger.info("All kiosk instances stopped.")

    def get_status(self) -> Dict[str, Dict]:
        """Returns status dictionary of running kiosks."""
        with self._lock:
            status = {}
            for seat_id, proc in self.running_processes.items():
                alive = proc.poll() is None
                status[seat_id] = {
                    "running": alive,
                    "pid": proc.pid if alive else None,
                    "window_id": self.window_ids.get(seat_id),
                    "url": self.kiosk_configs.get(seat_id, {}).get("kiosk_url", ""),
                }
            return status
