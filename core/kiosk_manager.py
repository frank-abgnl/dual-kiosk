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
        # Optional hook: callback(seat_id, window_id). Used to give the seat's
        # keyboard master focus on its kiosk window.
        self.on_window_ready = None
        self.overlay_seat: Optional[str] = None

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
        display_geometry = (width, height, x, y). The window is moved to that rectangle
        by the window manager; Firefox's own --kiosk-monitor is NOT used because it is
        unreliable on X11/Xwayland and fights with our placement.
        """
        with self._lock:
            if seat_id in self.running_processes:
                self._terminate_seat_process(seat_id)

            profile_path = self.setup_profile(seat_id, kiosk_url)

            cmd = [
                "firefox",
                "--new-instance",
                "--profile", str(profile_path),
                "--kiosk",
                kiosk_url,
            ]

            logger.info(f"Launching Firefox Kiosk for '{seat_id}' (geometry {display_geometry}): {' '.join(cmd)}")
            try:
                env = os.environ.copy()
                env.pop("WAYLAND_DISPLAY", None)  # Force X11 / Xwayland for xinput compatibility
                env["MOZ_ENABLE_WAYLAND"] = "0"
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

        # Always place the window on its monitor; lock flags only if enforce_lock.
        t = threading.Thread(
            target=self._enforce_window_after_launch,
            args=(seat_id, proc.pid, display_geometry, enforce_lock),
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
    # Window placement & constraints
    # -----------------------------------------------------------------------
    @staticmethod
    def _window_area(win_id: str) -> int:
        out, _, rc = run_command(["xdotool", "getwindowgeometry", "--shell", win_id])
        if rc != 0:
            return 0
        vals = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        try:
            return int(vals.get("WIDTH", 0)) * int(vals.get("HEIGHT", 0))
        except ValueError:
            return 0

    def _find_window_id_for_pid(self, pid: int, timeout_sec: int = 15) -> Optional[str]:
        """Finds the main visible X11 window of the process (largest one wins)."""
        start = time.time()
        while time.time() - start < timeout_sec:
            out, _, rc = run_command(["xdotool", "search", "--onlyvisible", "--pid", str(pid)])
            if rc == 0 and out:
                ids = [l.strip() for l in out.splitlines() if l.strip()]
                ids = [i for i in ids if self._window_area(i) > 200 * 200]
                if ids:
                    return max(ids, key=self._window_area)

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
        enforce_lock: bool = True,
    ):
        """Waits for the window, moves it onto its monitor, then applies lock flags."""
        win_id = self._find_window_id_for_pid(pid)
        if not win_id:
            logger.warning(f"Could not find window ID for seat '{seat_id}' (PID {pid}) within timeout.")
            return

        with self._lock:
            self.window_ids[seat_id] = win_id

        self._notify_window(seat_id, win_id)  # bind to seat's pointer/keyboard immediately
        time.sleep(1.0)  # let Firefox finish its own startup fullscreen first
        logger.info(f"Placing window {win_id} for seat '{seat_id}' at {display_geometry}...")
        self.place_window(win_id, display_geometry)
        if enforce_lock:
            self.lock_window(win_id)
        self._notify_window(seat_id, win_id)

    def _notify_window(self, seat_id: str, win_id: str):
        cb = self.on_window_ready
        if cb:
            try:
                cb(seat_id, win_id)
            except Exception as exc:
                logger.warning(f"on_window_ready failed for {seat_id}: {exc}")

    def place_window(self, win_id: str, display_geometry: Optional[Tuple[int, int, int, int]] = None):
        """
        Moves the window onto the target monitor and re-fullscreens it there.
        The WM fullscreens onto whichever monitor the window currently sits on,
        so the move must be applied (hence the short pauses) before fullscreen is re-added.
        """
        if display_geometry:
            w, h, x, y = display_geometry
            run_command(["wmctrl", "-i", "-r", win_id, "-b", "remove,fullscreen,maximized_vert,maximized_horz"])
            time.sleep(0.4)
            run_command(["wmctrl", "-i", "-r", win_id, "-e", f"0,{x},{y},{w},{h}"])
            run_command(["xdotool", "windowmove", win_id, str(x), str(y)])
            time.sleep(0.4)
        run_command(["wmctrl", "-i", "-r", win_id, "-b", "add,fullscreen"])

    def lock_window(self, win_id: str, display_geometry: Optional[Tuple[int, int, int, int]] = None):
        """Enforces always-on-top and strips close/minimize actions on an X11 window."""
        if display_geometry:
            self.place_window(win_id, display_geometry)

        run_command(["wmctrl", "-i", "-r", win_id, "-b", "add,fullscreen,above"])
        run_command(["xprop", "-id", win_id, "-set", "_NET_WM_ALLOWED_ACTIONS", "_NET_WM_ACTION_FULLSCREEN"])

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

                if win_id and (self.overlay_seat != seat_id):
                    self._notify_window(seat_id, win_id)  # keep per-seat keyboard focus pinned unless overlay is showing

                if win_id and cfg.get("enforce_lock", True):
                    out, _, rc = run_command(["xprop", "-id", win_id, "_NET_WM_STATE"])
                    if rc == 0 and "_NET_WM_STATE_HIDDEN" in out:
                        logger.warning(f"[Watchdog] Seat '{seat_id}' window was minimized! Restoring immediately...")
                        self.restore_and_unminimize(win_id)

    # -----------------------------------------------------------------------
    # Multi-Seat Launch Orchestrator
    # -----------------------------------------------------------------------
    @staticmethod
    def _geometry_for_seats(displays_info: Optional[List[Dict]]) -> List[Optional[Tuple[int, int, int, int]]]:
        """
        Resolves each seat's monitor geometry from the SAVED SEAT CONFIG (by output name),
        so the browser lands on the same monitor the seat's pointer is confined to.
        Falls back to display order only if the configured output is missing.
        """
        geoms: List[Optional[Tuple[int, int, int, int]]] = [None, None]
        if not displays_info:
            return geoms
        by_name = {d["name"]: d for d in displays_info}

        try:
            from core.config_manager import ConfigManager
            seats = ConfigManager().load_config().get("seats", [])
        except Exception:
            seats = []

        for i in range(2):
            d = None
            if i < len(seats):
                d = by_name.get(seats[i].get("display"))
            if d is None and i < len(displays_info):
                d = displays_info[i]
            if d and d.get("width") and d.get("height"):
                geoms[i] = (d["width"], d["height"], d["x"], d["y"])
        return geoms

    def launch_dual_kiosks(
        self,
        seat1_url: str = "https://duckduckgo.com",
        seat2_url: str = "https://www.google.com",
        displays_info: Optional[List[Dict]] = None,
        enforce_lock: bool = True,
    ) -> bool:
        """Launches Seat 1 and Seat 2 kiosks, each on the monitor configured for that seat."""
        geom1, geom2 = self._geometry_for_seats(displays_info)

        ok1 = self.launch_kiosk("seat1", seat1_url, monitor_index=0, display_geometry=geom1, enforce_lock=enforce_lock)
        time.sleep(1.5)  # stagger so window discovery doesn't race between the two instances
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

    def set_overlay_seat(self, seat_id: Optional[str]):
        """Sets which seat currently has an active control board overlay, pausing watchdog focus theft."""
        self.overlay_seat = seat_id
        logger.info(f"Kiosk watchdog overlay seat set to: {seat_id}")

    def set_kiosk_above(self, seat_id: str, above: bool):
        """Temporarily adds or removes the 'above' state from a seat's kiosk window."""
        win_id = self.window_ids.get(seat_id)
        if not win_id:
            self.discover_running_kiosks()
            win_id = self.window_ids.get(seat_id)
        if win_id:
            action = "add" if above else "remove"
            run_command(["wmctrl", "-i", "-r", win_id, "-b", f"{action},above"])
            logger.info(f"Set kiosk window {win_id} for seat '{seat_id}' above={above}")

    def discover_running_kiosks(self) -> Dict[str, Dict]:
        """
        Discovers active Firefox kiosk processes and windows on the system,
        populating window_ids even if this KioskManager didn't spawn them.
        """
        out, _, rc = run_command(["wmctrl", "-l", "-p"])
        if rc != 0 or not out:
            return {}

        discovered = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                win_id = parts[0]
                try:
                    pid = int(parts[2])
                except ValueError:
                    continue

                cmdline_path = Path(f"/proc/{pid}/cmdline")
                if cmdline_path.exists():
                    try:
                        cmdline = cmdline_path.read_bytes().decode("utf-8", errors="ignore").replace("\x00", " ")
                        if "firefox" in cmdline:
                            if "profiles/seat1" in cmdline:
                                self.window_ids["seat1"] = win_id
                                discovered["seat1"] = {"pid": pid, "window_id": win_id, "running": True}
                            elif "profiles/seat2" in cmdline:
                                self.window_ids["seat2"] = win_id
                                discovered["seat2"] = {"pid": pid, "window_id": win_id, "running": True}
                    except Exception:
                        pass
        return discovered

    def is_any_kiosk_running(self) -> bool:
        """Returns True if any kiosk instance is currently alive."""
        with self._lock:
            for p in self.running_processes.values():
                if p.poll() is None:
                    return True
        # Check system discovery
        discovered = self.discover_running_kiosks()
        return len(discovered) > 0

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
            if not status or not any(s.get("running") for s in status.values()):
                discovered = self.discover_running_kiosks()
                if discovered:
                    return discovered
            return status
