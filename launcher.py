#!/usr/bin/env python3
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

from core.paths import CONFIG_DIR, LOCK_FILE, PID_FILE, LOG_FILE, STATE_DIR, ensure_state_dir
from core.logging_setup import setup_logging

setup_logging()
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
    env["GDK_BACKEND"] = "x11"
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
    from core.preflight import preflight
    pref = preflight()
    for w in pref.warnings:
        log.warning(f"Preflight warning: {w}")
    if not pref.ok:
        for err in pref.errors:
            notify(f"Preflight error: {err}", urgent=True)
        return 1
    from core.supervisor import run
    return run(args)


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
