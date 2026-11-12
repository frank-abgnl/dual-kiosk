#!/usr/bin/env python3
"""
Autostart & Power Outage Recovery Service.
Handles automatic startup configuration via systemd user services and XDG autostart,
with automatic hardware reseating upon system power recovery.
"""

import os
import signal
import subprocess
import threading
import time
import logging
from pathlib import Path
from typing import Callable, Optional, Tuple

logger = logging.getLogger("xinput_seating.autostart")

PROJECT_DIR = Path(__file__).resolve().parent.parent
START_SH = PROJECT_DIR / "start.sh"
SERVICE_NAME = "dual-kiosk.service"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEMD_SERVICE_PATH = SYSTEMD_USER_DIR / SERVICE_NAME
XDG_AUTOSTART_DIR = Path.home() / ".config" / "autostart"
XDG_DESKTOP_PATH = XDG_AUTOSTART_DIR / "dual-kiosk.desktop"


def run_command(args, timeout=5) -> Tuple[str, str, int]:
    """Run shell command safely, returning (stdout, stderr, returncode)."""
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


def is_autostart_enabled() -> bool:
    """
    Checks if automatic launch on startup is enabled via systemd user service,
    XDG autostart desktop entry, or user configuration.
    """
    # Check systemctl user unit status
    out, _, rc = run_command(["systemctl", "--user", "is-enabled", SERVICE_NAME])
    if rc == 0 and "enabled" in out:
        return True

    # Check files presence
    if SYSTEMD_SERVICE_PATH.exists() or XDG_DESKTOP_PATH.exists():
        return True

    # Check configuration setting
    try:
        from core.config_manager import ConfigManager
        cfg = ConfigManager().load_config()
        return bool(cfg.get("settings", {}).get("auto_start_kiosks", False))
    except Exception:
        return False


def enable_autostart() -> bool:
    """
    Enables automatic launch on startup by installing the systemd user service,
    generating an XDG desktop autostart entry, and setting config flags.
    """
    logger.info("Enabling launch on startup for power outage recovery...")
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    XDG_AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)

    if not START_SH.is_file():
        logger.error(f"Cannot enable autostart: start.sh not found at {START_SH}")
        return False

    # Ensure executable permissions
    try:
        START_SH.chmod(START_SH.stat().st_mode | 0o755)
    except Exception as exc:
        logger.warning(f"Failed to chmod {START_SH}: {exc}")

    # 1. Systemd User Service
    service_contents = f"""[Unit]
Description=Dual Kiosk Auto-Start & Reseating Service
After=graphical-session.target xdg-desktop-autostart.target
Wants=graphical-session.target

[Service]
Type=simple
Environment="DISPLAY=:0"
ExecStart={START_SH} --auto-start
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""
    try:
        SYSTEMD_SERVICE_PATH.write_text(service_contents)
        logger.info(f"Systemd service file written to {SYSTEMD_SERVICE_PATH}")
    except Exception as exc:
        logger.error(f"Failed to write systemd service unit: {exc}")
        return False

    # 2. XDG Desktop Autostart Entry (fallback and broad desktop session compatibility)
    desktop_contents = f"""[Desktop Entry]
Type=Application
Name=Dual Kiosk Auto-Reseat & Launch
Comment=Automatically reseats displays and USB devices and launches kiosks on startup
Exec={START_SH} --auto-start
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
    try:
        XDG_DESKTOP_PATH.write_text(desktop_contents)
        logger.info(f"XDG autostart file written to {XDG_DESKTOP_PATH}")
    except Exception as exc:
        logger.warning(f"Failed to write XDG autostart desktop file: {exc}")

    # 3. Reload systemd user daemon and enable service
    run_command(["systemctl", "--user", "daemon-reload"])
    out, err, rc = run_command(["systemctl", "--user", "enable", SERVICE_NAME])
    if rc != 0:
        logger.warning(f"systemctl --user enable warning: {err or out}")

    # 4. Save state to config
    try:
        from core.config_manager import ConfigManager
        cfg_mgr = ConfigManager()
        cfg = cfg_mgr.load_config()
        if "settings" not in cfg:
            cfg["settings"] = {}
        cfg["settings"]["auto_start_kiosks"] = True
        cfg_mgr.save_config(cfg)
    except Exception as exc:
        logger.warning(f"Could not update config file for autostart: {exc}")

    return True


def disable_autostart() -> bool:
    """
    Disables automatic launch on startup and cleans up services and autostart files.
    """
    logger.info("Disabling launch on startup...")
    # Stop and disable systemd user unit
    run_command(["systemctl", "--user", "disable", "--now", SERVICE_NAME])

    if SYSTEMD_SERVICE_PATH.exists():
        try:
            SYSTEMD_SERVICE_PATH.unlink()
        except Exception as exc:
            logger.warning(f"Could not remove {SYSTEMD_SERVICE_PATH}: {exc}")

    if XDG_DESKTOP_PATH.exists():
        try:
            XDG_DESKTOP_PATH.unlink()
        except Exception as exc:
            logger.warning(f"Could not remove {XDG_DESKTOP_PATH}: {exc}")

    run_command(["systemctl", "--user", "daemon-reload"])

    # Update config setting
    try:
        from core.config_manager import ConfigManager
        cfg_mgr = ConfigManager()
        cfg = cfg_mgr.load_config()
        if "settings" not in cfg:
            cfg["settings"] = {}
        cfg["settings"]["auto_start_kiosks"] = False
        cfg_mgr.save_config(cfg)
    except Exception as exc:
        logger.warning(f"Could not update config file for autostart: {exc}")

    return True


def toggle_autostart() -> bool:
    """Toggles autostart status and returns the new enabled state."""
    if is_autostart_enabled():
        disable_autostart()
        return False
    else:
        enable_autostart()
        return True


def install_user_service() -> bool:
    """Backward-compatible alias for enable_autostart()."""
    return enable_autostart()


def run_startup_reseat_and_launch(
    wait_timeout_sec: int = 20,
    stop_event: Optional[threading.Event] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """
    Executed on system boot / power recovery.
    Loops start_session with exponential backoff on retryable failures.
    Polls for a stop request using a threading.Event set by SIGTERM/SIGINT handlers.
    When stopped by signal, calls stop_session and returns 0.
    Returns 1 if startup encounters an unrecoverable error.
    """
    from core.session_manager import session_mgr, StartResult
    from core.hotkey_manager import HotkeyManager

    logger.info("Starting automated power outage recovery / boot reseating sequence...")

    stop = stop_event or threading.Event()
    if threading.current_thread() is threading.main_thread():
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())
        except Exception as exc:
            logger.debug(f"Could not register signal handlers: {exc}")

    delay = 2
    res = None
    while not stop.is_set():
        res = session_mgr.start_session(on_event=logger.info)
        if res.ok:
            break
        if res.code in (StartResult.NEED_DISPLAYS, StartResult.NEED_DEVICES, StartResult.KIOSK_FAILED):
            logger.warning(f"Startup attempt failed ({res.code}: {res.message}), retrying in {delay}s...")
            sleep_fn(delay)
            delay = min(delay * 2, 60)
        else:
            logger.error(f"Startup unrecoverable error ({res.code}): {res.message}")
            return 1

    if stop.is_set():
        session_mgr.stop_session(exit_gui=True, on_event=logger.info)
        return 0

    if not res or not res.ok:
        logger.error(f"Failed to launch kiosks on startup: {res.message if res else 'Unknown'}")
        return 1

    logger.info("Dual kiosks launched successfully on startup!")
    session_mgr.hotkey_mgr = HotkeyManager(launch_gui_if_needed=True, seat_keyboards=session_mgr.engine.seat_keyboards)
    session_mgr.hotkey_mgr.start()
    logger.info("Super Keyboard Shortcut (Ctrl+Shift+M+S) listener active for kiosk overlay.")

    try:
        while not stop.is_set():
            time.sleep(2)
            seats = session_mgr.cfg_mgr.load_config().get("seats", [])
            reseated = session_mgr.engine.check_and_reseat_hotplugged_devices(seats)
            if reseated:
                for m in reseated:
                    logger.info(f"[Autostart Hotplug Reseat] {m}")
    except (KeyboardInterrupt, SystemExit):
        stop.set()

    logger.info("Stopping kiosks, display guard, hotkey listener and resetting seating...")
    session_mgr.stop_session(exit_gui=True, on_event=logger.info)
    return 0
