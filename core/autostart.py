#!/usr/bin/env python3
"""
Autostart & Power Outage Recovery Service.
Handles automatic startup configuration via systemd user services and XDG autostart,
with automatic hardware reseating upon system power recovery.
"""

import os
import time
import subprocess
import logging
from pathlib import Path
from typing import Optional, Tuple

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


def run_startup_reseat_and_launch(wait_timeout_sec: int = 20) -> bool:
    """
    Executed on system boot / power recovery.
    1. Communicates with system to wait for X11/Xwayland server readiness.
    2. Auto-identifies screen resolutions and connected USB input devices (mouses & keyboards).
    3. Reseats hardware to Seat 1 and Seat 2 and saves configuration.
    4. Applies XInput pseudo-seating and launches dual Firefox kiosks.
    5. Keeps supervisor alive.
    """
    from core.device_manager import DeviceManager
    from core.xinput_seating import XInputSeatingEngine
    from core.kiosk_manager import KioskManager
    from core.config_manager import ConfigManager

    logger.info("Starting automated power outage recovery / boot reseating sequence...")

    dev_mgr = DeviceManager()
    cfg_mgr = ConfigManager()
    engine = XInputSeatingEngine()
    kiosk_mgr = KioskManager()
    kiosk_mgr.on_window_ready = engine.focus_seat_keyboard

    # 1. Wait for display server to be ready
    start_time = time.time()
    displays = []
    total = {"width": 1920, "height": 1080}
    while time.time() - start_time < wait_timeout_sec:
        displays, total = dev_mgr.get_displays()
        active_disp = [d for d in displays if d.get("active") and d.get("width", 0) > 0]
        if active_disp:
            break
        time.sleep(1)

    logger.info(f"Detected {len(displays)} display(s): {[d['name'] + ' (' + str(d['width']) + 'x' + str(d['height']) + ')' for d in displays]}")

    # 2. Auto-identify screen resolutions & assign connected USB devices
    logger.info("Communicating with system to auto-identify screens and connected USB devices...")
    cfg = cfg_mgr.auto_generate_config(dev_mgr)
    seats = cfg.get("seats", [])

    for s in seats:
        logger.info(
            f"Seat '{s.get('id')}': Display={s.get('display')}, "
            f"Mouse={s.get('mouse_name')} (ID {s.get('mouse_id')}), "
            f"Keyboard={s.get('keyboard_name')} (ID {s.get('keyboard_id')})"
        )

    # 3. Apply XInput seating
    logger.info("Applying XInput MPX master pairs and device reattachments...")
    results = engine.apply_seating(seats, displays, (total["width"], total["height"]))
    logger.info(f"XInput mapping results: {results}")

    # 4. Launch kiosks
    s1_url = seats[0].get("kiosk_url", "https://duckduckgo.com") if len(seats) > 0 else "https://duckduckgo.com"
    s2_url = seats[1].get("kiosk_url", "https://www.google.com") if len(seats) > 1 else "https://www.google.com"

    logger.info("Launching dual Firefox kiosks with isolated profiles...")
    ok = kiosk_mgr.launch_dual_kiosks(s1_url, s2_url, displays, enforce_lock=True)

    if ok:
        logger.info("Dual kiosks launched successfully on startup!")
        try:
            while True:
                time.sleep(2)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Stopping kiosks and resetting seating...")
            kiosk_mgr.stop_all_kiosks()
            engine.reset_all_to_core()
    else:
        logger.error("Failed to launch kiosks on startup.")

    return ok
