#!/usr/bin/env python3
"""
Uninstaller for XInput Pseudo-Seating.

Restores the machine to its pre-setup state:
  1. Stops any running supervisor (GUI / `--start` CLI) so nothing respawns.
  2. Terminates all Firefox kiosks that use our profiles.
  3. Reattaches every input device to the Virtual core masters, resets
     coordinate transformation matrices and removes custom master pairs.
  4. Removes config, profiles and launcher/autostart entries we may have created.

Only depends on the standard library (no GTK needed).
"""

import os
import shutil
import signal
import subprocess
import time
import logging
from pathlib import Path
from typing import Iterator, List, Tuple

from core.xinput_seating import XInputSeatingEngine

logger = logging.getLogger("xinput_seating.uninstall")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = PROJECT_ROOT / "main.py"
CONFIG_DIR = Path.home() / ".config" / "xinput-seats"
PROFILES_DIR = CONFIG_DIR / "profiles"

DESKTOP_NAMES = ("xinput-multiseat.desktop", "xinput-kiosk-launcher.desktop", "dual-kiosk.desktop")
LAUNCHER_FILES = [
    base / name
    for base in (
        Path.home() / ".local" / "share" / "applications",
        Path.home() / ".config" / "autostart",
        Path.home() / "Desktop",
    )
    for name in DESKTOP_NAMES
]
SYSTEMD_UNITS = ("dual-kiosk.service", "xinput-multiseat.service")


def _run(args, timeout=5) -> Tuple[str, int]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip(), p.returncode
    except Exception:
        return "", -1


def _iter_procs() -> Iterator[Tuple[int, List[str], str]]:
    """Yields (pid, argv, cwd) for every readable process."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        if argv:
            yield int(entry.name), argv, cwd


class Uninstaller:
    def __init__(self, keep_config: bool = False, dry_run: bool = False):
        self.keep_config = keep_config
        self.dry_run = dry_run
        self.report: List[str] = []

    def _say(self, msg: str):
        self.report.append(msg)
        print(msg)

    # ------------------------------------------------------------------
    # Process discovery / termination
    # ------------------------------------------------------------------
    def find_supervisors(self) -> List[int]:
        """Other python processes running this project's main.py (GUI or --start)."""
        me = os.getpid()
        found = []
        for pid, argv, cwd in _iter_procs():
            if pid == me or "python" not in os.path.basename(argv[0]):
                continue
            for a in argv[1:]:
                if not (a.endswith("main.py") or a.endswith("launcher.py")):
                    continue
                p = Path(a) if os.path.isabs(a) else Path(cwd) / a
                try:
                    if p.resolve() in (PROJECT_ROOT / "main.py", PROJECT_ROOT / "launcher.py"):
                        found.append(pid)
                        break
                except OSError:
                    pass
        return found

    def find_kiosks(self) -> List[int]:
        """Firefox processes launched with one of our isolated profiles."""
        me = os.getpid()
        prof = str(PROFILES_DIR)
        return [
            pid for pid, argv, _ in _iter_procs()
            if pid != me and "firefox" in " ".join(argv).lower() and prof in " ".join(argv)
        ]

    def _terminate(self, pids: List[int], label: str):
        if not pids:
            self._say(f"    No {label} running.")
            return
        self._say(f"    Terminating {label}: {pids}")
        if self.dry_run:
            return
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.time() + 4
        while time.time() < deadline:
            if not any(self._alive(p) for p in pids):
                return
            time.sleep(0.2)
        for pid in pids:
            if self._alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            with open(f"/proc/{pid}/stat") as f:
                return f.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    # ------------------------------------------------------------------
    # XInput reset
    # ------------------------------------------------------------------
    def reset_xinput(self):
        if shutil.which("xinput") is None:
            self._say("    xinput not found; skipping device reset.")
            return
        if self.dry_run:
            self._say("    Would reattach all devices to Virtual core and remove custom masters.")
            return
        XInputSeatingEngine().reset_all_to_core()

        # Pick up any devices left floating after master removal.
        out, rc = _run(["xinput", "list"])
        if rc == 0:
            for line in out.splitlines():
                if "[floating slave]" not in line or "XTEST" in line:
                    continue
                dev_id = line.split("id=")[1].split()[0]
                # Class is unknown from the listing; wrong-class attempt fails harmlessly.
                for master in (XInputSeatingEngine.CORE_POINTER_ID, XInputSeatingEngine.CORE_KEYBOARD_ID):
                    _run(["xinput", "reattach", dev_id, str(master)])
        self._say("    XInput devices restored to Virtual core.")

    # ------------------------------------------------------------------
    # File cleanup
    # ------------------------------------------------------------------
    def _remove(self, path: Path):
        if not (path.exists() or path.is_symlink()):
            return
        self._say(f"    Removing {path}")
        if self.dry_run:
            return
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            self._say(f"    ! Could not remove {path}: {exc}")

    def remove_files(self):
        if self.keep_config:
            self._say(f"    Keeping {CONFIG_DIR} (--keep-config).")
        else:
            self._remove(CONFIG_DIR)

        systemd_dir = Path.home() / ".config" / "systemd" / "user"
        for unit in SYSTEMD_UNITS:
            unit_file = systemd_dir / unit
            if unit_file.exists() and not self.dry_run:
                _run(["systemctl", "--user", "disable", "--now", unit])
            self._remove(unit_file)
        if systemd_dir.exists() and not self.dry_run:
            _run(["systemctl", "--user", "daemon-reload"])

        for f in LAUNCHER_FILES:
            self._remove(f)
        if not self.dry_run and shutil.which("update-desktop-database"):
            _run(["update-desktop-database", str(Path.home() / ".local" / "share" / "applications")])

        for cache in PROJECT_ROOT.rglob("__pycache__"):
            self._remove(cache)

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def run(self) -> bool:
        tag = " (dry run)" if self.dry_run else ""
        self._say(f"[1/4] Stopping supervisors{tag}...")
        self._terminate(self.find_supervisors(), "supervisor/GUI processes")

        self._say(f"[2/4] Stopping Firefox kiosks{tag}...")
        self._terminate(self.find_kiosks(), "kiosk browsers")

        self._say(f"[3/4] Resetting XInput seating{tag}...")
        self.reset_xinput()

        self._say(f"[4/4] Removing configuration and launchers{tag}...")
        self.remove_files()

        self._say("[✓] Uninstall complete. System is back to its pre-setup state." if not self.dry_run
                  else "[✓] Dry run finished; nothing was changed.")
        return True
