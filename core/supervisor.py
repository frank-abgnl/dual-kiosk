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
from core.paths import LOCK_FILE, LOG_FILE, PID_FILE, STATE_DIR, SPAWN_GUARD_FILE, ensure_state_dir
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
    env["GDK_BACKEND"] = "x11"
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


def check_spawn_guard(cooldown: float = 10.0, sleep_fn=time.sleep):
    """Prevents rapid respawn loops if supervisor is crashing repeatedly."""
    now = time.time()
    try:
        if SPAWN_GUARD_FILE.exists():
            last = float(SPAWN_GUARD_FILE.read_text().strip())
            elapsed = now - last
            if elapsed < cooldown:
                wait_time = cooldown - elapsed
                logger.warning(f"Spawn guard active: restarted within {elapsed:.1f}s. Pausing {wait_time:.1f}s...")
                sleep_fn(wait_time)
        SPAWN_GUARD_FILE.write_text(str(time.time()))
    except Exception as exc:
        logger.debug(f"Spawn guard notice: {exc}")


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

    from core.preflight import preflight
    pref = preflight()
    for w in pref.warnings:
        logger.warning(f"Preflight warning: {w}")
    if not pref.ok:
        for err in pref.errors:
            logger.error(f"Preflight error: {err}")
            notify(f"Preflight error: {err}", urgent=True)
        return 1

    lock = acquire_lock(lock_path)
    if lock is None:
        logger.warning("Another supervisor is already running; exiting.")
        notify("Kiosks are already running - opening the Control Board.")
        if getattr(args, "seat", None):
            cmd_control_board(args.seat)
        return 0

    check_spawn_guard(10.0, sleep_fn=sleep_fn)

    stop = stop_event or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, lambda *_: stop.set())
            except Exception:
                pass
        reseat_flag = threading.Event()
        hard_reseat_flag = threading.Event()
        try:
            signal.signal(signal.SIGUSR1, lambda *_: reseat_flag.set())
            signal.signal(signal.SIGUSR2, lambda *_: hard_reseat_flag.set())
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
                if sleep_fn is not time.sleep:
                    sleep_fn(delay)
                else:
                    if stop.wait(delay):
                        break
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
            seat_keyboards=lambda: session_mgr.engine.seat_keyboards,
            on_activity=session_mgr.kiosk.record_input,
        )
        session_mgr.hotkey_mgr.start()

        # Monitoring and hotplug reseat loop
        last_hotplug = 0.0
        while not stop.wait(0.4):
            if hard_reseat_flag.is_set():
                hard_reseat_flag.clear()
                session_mgr.reseat_session(hard=True, on_event=notify)
            elif reseat_flag.is_set():
                reseat_flag.clear()
                session_mgr.reseat_session(hard=False, on_event=notify)

            if gui_proc is not None and gui_proc.poll() is not None:
                gui_proc = None
            if time.time() - last_hotplug >= 1.5:
                last_hotplug = time.time()
                cfg = session_mgr.cfg_mgr.load_config()
                seats = cfg.get("seats", [])
                settings = cfg.get("settings", {})
                if settings.get("hotplug_auto_reseat", True):
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
