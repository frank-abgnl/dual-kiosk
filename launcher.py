#!/usr/bin/env python3
"""
One-click Kiosk Launcher (use from a desktop / menu shortcut instead of autostart).

    launch.sh                 detect hardware -> assign seats -> save config -> launch kiosks
    launch.sh --reassign      forget saved seat assignment and detect from scratch (restarts)
    launch.sh --restart       stop a running session first, then launch again
    launch.sh --no-detect     launch with the saved config only
    launch.sh --reseat        re-seat all devices of a running session (same as Ctrl+Shift+M+R)
    launch.sh --reseat --hard ... and restart the browsers too
    launch.sh --stop          stop kiosks, reset input devices to Virtual core
    launch.sh --control-board show the Control Board (also what a second click on the shortcut does)

The process stays alive as the supervisor (watchdog, shield, hotplug handling, hotkey).
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


def spawn_control_board(seat: str):
    env = os.environ.copy()
    env.pop("WAYLAND_DISPLAY", None)
    subprocess.Popen([sys.executable, str(PROJECT_DIR / "main.py"), "--overlay", "--seat", seat],
                     env=env, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_control_board(seat: str) -> int:
    from core.hotkey_manager import send_overlay_command
    if not send_overlay_command(f"SHOW {seat}"):
        spawn_control_board(seat)
    return 0


def emergency_cleanup():
    """Kills leftover kiosk browsers and returns every input device to Virtual core."""
    from core.uninstaller import Uninstaller
    from core.xinput_seating import XInputSeatingEngine
    from core import overlay_state
    u = Uninstaller()
    u._terminate(u.find_kiosks(), "kiosk browsers")
    XInputSeatingEngine().reset_all_to_core()
    overlay_state.clear()


def cmd_stop() -> int:
    pid = _read_pid()
    if pid:
        log.info(f"Stopping launcher session (pid {pid})...")
        os.kill(pid, signal.SIGTERM)
        for _ in range(60):
            if not _pid_alive(pid):
                break
            time.sleep(0.25)
        else:
            os.kill(pid, signal.SIGKILL)
    emergency_cleanup()
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


def wait_for_display(dm, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        displays, total = dm.get_displays()
        if any(d.get("active") and d.get("width", 0) > 0 for d in displays):
            return displays, total
        time.sleep(1)
    return dm.get_displays()


def detect_and_assign(cfg_mgr, dm, engine, reassign: bool):
    """Detects displays + peripherals, assigns them to seats and saves the result."""
    cfg = cfg_mgr.load_config() if cfg_mgr.config_path.exists() else {}
    seats = cfg.get("seats") or []
    has_memory = len(seats) >= 2 and any(s.get("mouse_name") or s.get("keyboard_name") for s in seats[:2])

    if reassign or not has_memory:
        log.info("Detecting hardware and assigning seats from scratch...")
        cfg = cfg_mgr.auto_generate_config(dm, force_reassign=True)
    else:
        log.info("Using the saved seat assignment (peripherals matched by name / USB identity)...")

    # A saved output name that no longer exists (e.g. renamed connector) -> map seats by monitor order
    displays, _ = dm.get_displays()
    names = [d["name"] for d in displays]
    for i, s in enumerate(cfg["seats"][:2]):
        if names and s.get("display") not in names:
            new = names[min(i, len(names) - 1)]
            log.warning(f"{s.get('id')}: display {s.get('display')!r} not found, using {new!r}")
            s["display"] = new
            s["locked_display"] = new

    resolved, assignments = engine.resolve_live(cfg["seats"])
    for s in resolved:  # persist stable identity, drop transient flags
        s.pop("pointer_connected", None)
        s.pop("keyboard_connected", None)
    cfg["seats"] = resolved
    cfg_mgr.save_config(cfg)

    for (sid, cls), dev in sorted(assignments.items()):
        seat = next((x for x in cfg["seats"] if x.get("id") == sid), {})
        log.info(f"  {sid} {cls}: " + (f"{dev['name']} (ID {dev['id']})" if dev else "not connected")
                 + f"   display={seat.get('display')}")
    return cfg


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
    PID_FILE.write_text(str(os.getpid()))

    from core.combo_hotkey import ComboHotkey
    from core.config_manager import ConfigManager
    from core.device_manager import DeviceManager
    from core.hotkey_manager import HotkeyManager, send_overlay_command
    from core.kiosk_manager import KioskManager
    from core.xinput_seating import XInputSeatingEngine

    dm, cfg_mgr, engine, kiosk = DeviceManager(), ConfigManager(), XInputSeatingEngine(), KioskManager()
    kiosk.on_window_ready = engine.focus_seat_keyboard
    kiosk.engine = engine

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())
    reseat_req = threading.Event()
    reseat_hard = [False]

    def request_reseat(hard=False):
        reseat_hard[0] = reseat_hard[0] or hard
        reseat_req.set()

    signal.signal(signal.SIGUSR1, lambda *_: request_reseat(False))
    signal.signal(signal.SIGUSR2, lambda *_: request_reseat(True))

    hotkey = reseat_key = None
    try:
        notify("Detecting displays and input devices...")
        emergency_cleanup()  # clean slate: no stale masters, kiosks or barriers
        displays, total = wait_for_display(dm)
        if not displays:
            notify("No display detected.", urgent=True)
            return 1

        cfg = args.no_detect and cfg_mgr.load_config() or detect_and_assign(cfg_mgr, dm, engine, args.reassign)
        seats = cfg.get("seats", [])
        displays, total = dm.get_displays()
        if len(displays) < 2:
            log.warning("Only one display detected: both seats will share it.")

        results = engine.apply_seating(seats, displays, (total["width"], total["height"]))
        log.info(f"Seating applied: {results}")

        def launch_kiosks(seats, displays):
            urls = [s.get("kiosk_url") for s in seats[:2]] + [None, None]
            return kiosk.launch_dual_kiosks(urls[0] or "https://duckduckgo.com",
                                            urls[1] or "https://www.google.com", displays, enforce_lock=True)

        ok = launch_kiosks(seats, displays)
        notify("Kiosks running. Ctrl+Shift+M+S opens the Control Board." if ok
               else "A kiosk failed to start - see ~/.config/xinput-seats/launcher.log", urgent=not ok)

        gui_until = [0.0]

        def on_hotkey(seat_id, _master, _slave):
            # The overlay GUI listens to the same hotkey itself; only start it if it is not running.
            if send_overlay_command("NOP"):  # socket answers -> GUI alive, it toggles itself
                return
            if time.time() > gui_until[0]:
                gui_until[0] = time.time() + 8
                spawn_control_board(seat_id)

        hotkey = HotkeyManager(on_trigger=on_hotkey, seat_keyboards=engine.seat_keyboards)
        hotkey.start()
        # Ctrl+Shift+M+R: re-seat everything without opening the GUI (handled by the main loop below)
        reseat_key = ComboHotkey("r", lambda *_: request_reseat(False),
                                 seat_keyboards=engine.seat_keyboards, name="reseat")
        reseat_key.start()

        def do_reseat(hard: bool):
            """Soft: reset + reassign devices, browsers keep running. Hard: browsers restart too."""
            try:
                send_overlay_command("HIDE")  # never leave the Control Board in the way
                alive = (len(kiosk.running_processes) >= 2
                         and all(p.poll() is None for p in kiosk.running_processes.values()))
                hard = hard or not alive
                notify("Re-seating all seats" + (" and restarting the browsers..." if hard else "..."))
                if hard:
                    kiosk.stop_all_kiosks()
                engine.reset_all_to_core()
                displays_now, total_now = dm.get_displays()
                cfg_now = detect_and_assign(cfg_mgr, dm, engine, False)
                seats_now = cfg_now.get("seats", [])
                res = engine.apply_seating(seats_now, displays_now, (total_now["width"], total_now["height"]))
                log.info(f"Re-seat result: {res}")
                hotkey.update_seat_keyboards(engine.seat_keyboards)
                reseat_key.update_seat_keyboards(engine.seat_keyboards)
                if hard:
                    launch_kiosks(seats_now, displays_now)
                else:  # rebind each browser to its seat's (new) master pointer + keyboard
                    for sid, win in list(kiosk.window_ids.items()):
                        engine.focus_seat_keyboard(sid, win)
                notify("Seats re-assigned.")
            except Exception as exc:
                log.exception("Re-seat failed")
                notify(f"Re-seat failed: {exc} (try launch.sh --reseat --hard)", urgent=True)

        last_hotplug = 0.0
        while not stop.wait(0.4):
            if reseat_req.is_set():
                hard = reseat_hard[0]
                reseat_req.clear()
                reseat_hard[0] = False
                do_reseat(hard)
                continue
            if time.time() - last_hotplug >= 1.5:
                last_hotplug = time.time()
                for msg in engine.check_and_reseat_hotplugged_devices(cfg_mgr.load_config().get("seats", [])):
                    notify(msg)
        return 0
    except Exception as exc:
        log.exception("Launcher failed")
        notify(f"Launcher failed: {exc}", urgent=True)
        return 1
    finally:
        log.info("Shutting down: stopping shield, kiosks and restoring devices...")
        if hotkey:
            hotkey.stop()
        if reseat_key:
            reseat_key.stop()
        kiosk.stop_all_kiosks()
        engine.reset_all_to_core()
        try:
            PID_FILE.unlink()
        except OSError:
            pass


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
