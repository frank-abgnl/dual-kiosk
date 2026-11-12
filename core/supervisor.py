#!/usr/bin/env python3
"""
Single supervisor implementation for dual kiosk pseudo-seating.
Acquires a non-blocking flock, manages the session lifecycle, runs the
sole HotkeyManager for Ctrl+Shift+M+S, and handles hotplug monitoring.
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
from typing import Optional

from core.hotkey_manager import HotkeyManager, send_overlay_command
from core.paths import LOCK_FILE, LOG_FILE, PID_FILE, STATE_DIR, ensure_state_dir
from core.session_manager import session_mgr, StartResult

logger = logging.getLogger("xinput_seating.supervisor")
PROJECT_DIR = Path(__file__).resolve().parent.parent
MAIN_PY = PROJECT_DIR / "main.py"
REQUIRED_TOOLS = ["xinput", "xrandr", "firefox", "wmctrl", "xdotool"]


def notify(msg: str, urgent: bool = False):
    """Logs message and sends desktop notification if available."""
    logger.info(msg)
    if shutil.which("notify-send") and os.environ.get("DISPLAY"):
        try:
            subprocess.Popen(
                [
                    "notify-send",
                    "-a", "Kiosk Launcher",
                    "-u", "critical" if urgent else "normal",
                    "-t", "6000",
                    "XInput Kiosk",
                    msg,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def acquire_lock(lock_path: Optional[Path] = None):
    """
    Acquires an exclusive non-blocking lock on LOCK_FILE (or lock_path).
    Never deletes or unlinks the lock file to preserve inode stability.
    """
    target = lock_path or LOCK_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = open(target, "a+")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        fd.close()
        return None


def spawn_control_board(seat: str = "seat1", start_hidden: bool = False):
    """Spawns the GTK3 Control Board GUI."""
    env = os.environ.copy()
    env.pop("WAYLAND_DISPLAY", None)
    cmd = [sys.executable, str(MAIN_PY), "--seat", seat]
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
    """Shows the control board via IPC or spawns it."""
    if not send_overlay_command(f"SHOW {seat}"):
        spawn_control_board(seat)
    return 0


def run(
    args=None,
    lock_path: Optional[Path] = None,
    sleep_fn=time.sleep,
    stop_event: Optional[threading.Event] = None,
) -> int:
    """
    Main supervisor execution routine.
    Used by launcher.py and main.py --auto-start.
    """
    if args is None:
        args = argparse.Namespace(
            reassign=False,
            restart=False,
            no_detect=False,
            seat="seat1",
        )

    ensure_state_dir()

    missing = [c for c in REQUIRED_TOOLS if not shutil.which(c)]
    if missing:
        notify(f"Missing tools: {', '.join(missing)}. Install them and try again.", urgent=True)
        return 1

    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"

    lock = acquire_lock(lock_path)
    if lock is None:
        logger.warning("Another supervisor is already running; exiting.")
        notify("Kiosks are already running - opening the Control Board.")
        if getattr(args, "seat", None):
            cmd_control_board(args.seat)
        return 0

    stop = stop_event or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, lambda *_: stop.set())
            except Exception:
                pass
        try:
            signal.signal(signal.SIGUSR1, lambda *_: session_mgr.reseat_session(hard=False, on_event=notify))
            signal.signal(signal.SIGUSR2, lambda *_: session_mgr.reseat_session(hard=True, on_event=notify))
        except Exception:
            pass

    gui_proc = None
    try:
        # Loop start_session with exponential backoff on retryable failures
        delay = 2
        res = None
        while not stop.is_set():
            res = session_mgr.start_session(
                reassign=getattr(args, "reassign", False),
                no_detect=getattr(args, "no_detect", False),
                on_event=notify,
            )
            if res.ok:
                break
            if res.code in (StartResult.NEED_DISPLAYS, StartResult.NEED_DEVICES, StartResult.KIOSK_FAILED):
                notify(f"Startup attempt failed ({res.code}: {res.message}), retrying in {delay}s...")
                sleep_fn(delay)
                delay = min(delay * 2, 60)
            else:
                notify(f"Unrecoverable startup error ({res.code}): {res.message}", urgent=True)
                return 1

        if stop.is_set():
            session_mgr.stop_session(exit_gui=True, on_event=notify)
            return 0

        # Bind GUI startup: spawn the Control Board GUI alongside the desktop session if not already open
        if not send_overlay_command("PING"):
            gui_proc = spawn_control_board(getattr(args, "seat", "seat1"), start_hidden=True)

        notify("Kiosks running. Ctrl+Shift+M+S opens the Control Board." if res.ok
               else f"A kiosk failed to start - see {LOG_FILE}", urgent=not res.ok)

        # Single HotkeyManager for the S combo
        gui_until = [0.0]

        def on_hotkey(seat_id, _master, _slave):
            if send_overlay_command("PING"):
                send_overlay_command(f"TOGGLE {seat_id}")
            else:
                if time.time() > gui_until[0]:
                    gui_until[0] = time.time() + 8
                    spawn_control_board(seat_id)

        session_mgr.hotkey_mgr = HotkeyManager(
            on_trigger=on_hotkey,
            seat_keyboards=session_mgr.engine.seat_keyboards,
        )
        session_mgr.hotkey_mgr.start()

        # Monitoring and hotplug reseat loop
        last_hotplug = 0.0
        while not stop.wait(0.4):
            if gui_proc is not None and gui_proc.poll() is not None:
                gui_proc = None
            if time.time() - last_hotplug >= 1.5:
                last_hotplug = time.time()
                seats = session_mgr.cfg_mgr.load_config().get("seats", [])
                for msg in session_mgr.engine.check_and_reseat_hotplugged_devices(seats):
                    notify(msg)
        return 0
    except Exception as exc:
        logger.exception(f"Supervisor failed: {exc}")
        notify(f"Supervisor failed: {exc}", urgent=True)
        return 1
    finally:
        logger.info("Shutting down supervisor session...")
        session_mgr.stop_session(exit_gui=True, on_event=logger.info)
        try:
            lock.close()
        except Exception:
            pass
