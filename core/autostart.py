#!/usr/bin/env python3
"""
Autostart & Power Outage Recovery Service.
Handles automatic startup configuration via systemd user services and XDG autostart,
with automatic hardware reseating upon system power recovery.
"""

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

logger = logging.getLogger("xinput_seating.autostart")

PROJECT_DIR = Path(__file__).resolve().parent.parent
START_SH = PROJECT_DIR / "start.sh"
LAUNCH_SH = PROJECT_DIR / "launch.sh"
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


_autostart_cache: Optional[bool] = None
_autostart_cache_ts: float = 0.0
AUTOSTART_CACHE_TTL: float = 30.0


def is_autostart_enabled(force: bool = False) -> bool:
    """
    Checks if automatic launch on startup is enabled via systemd user service,
    XDG autostart desktop entry, or user configuration.
    Caches result for 30s unless force=True.
    """
    global _autostart_cache, _autostart_cache_ts
    now = time.time()
    if not force and _autostart_cache is not None and (now - _autostart_cache_ts < AUTOSTART_CACHE_TTL):
        return _autostart_cache

    # Check systemctl user unit status
    out, _, rc = run_command(["systemctl", "--user", "is-enabled", SERVICE_NAME])
    if rc == 0 and "enabled" in out:
        _autostart_cache = True
        _autostart_cache_ts = now
        return True

    # Check files presence
    if SYSTEMD_SERVICE_PATH.exists() or XDG_DESKTOP_PATH.exists():
        _autostart_cache = True
        _autostart_cache_ts = now
        return True

    # Check configuration setting
    try:
        from core.config_manager import ConfigManager
        cfg = ConfigManager().load_config()
        res = bool(cfg.get("settings", {}).get("auto_start_kiosks", False))
    except Exception:
        res = False

    _autostart_cache = res
    _autostart_cache_ts = now
    return res


def is_systemctl_user_available() -> bool:
    """Checks if systemctl --user is functional in the current environment."""
    if shutil.which("systemctl") is None:
        return False
    _, _, rc = run_command(["systemctl", "--user", "status"])
    return rc in (0, 3)


def enable_autostart() -> bool:
    """
    Enables automatic launch on startup.
    Writes only the systemd user unit if systemctl --user is available.
    Writes the XDG desktop autostart entry only if systemctl --user is unavailable.
    """
    logger.info("Enabling launch on startup for power outage recovery...")

    target_script = LAUNCH_SH if LAUNCH_SH.is_file() else START_SH
    if not target_script.is_file():
        logger.error(f"Cannot enable autostart: launcher script not found at {target_script}")
        return False

    # Ensure executable permissions
    try:
        target_script.chmod(target_script.stat().st_mode | 0o755)
    except Exception as exc:
        logger.warning(f"Failed to chmod {target_script}: {exc}")

    use_systemd = is_systemctl_user_available()

    if use_systemd:
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        # 1. Systemd User Service: ExecStart="<path>/launch.sh" (quoted)
        service_contents = f"""[Unit]
Description=Dual Kiosk Auto-Start & Reseating Service
After=graphical-session.target
Wants=graphical-session.target

[Service]
Type=simple
Environment="DISPLAY=:0"
ExecStart="{target_script}"
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
"""
        try:
            SYSTEMD_SERVICE_PATH.write_text(service_contents)
            logger.info(f"Systemd service file written to {SYSTEMD_SERVICE_PATH}")
        except Exception as exc:
            logger.error(f"Failed to write systemd service unit: {exc}")
            return False

        # Reload systemd user daemon and enable service
        run_command(["systemctl", "--user", "daemon-reload"])
        out, err, rc = run_command(["systemctl", "--user", "enable", SERVICE_NAME])
        if rc != 0:
            logger.warning(f"systemctl --user enable warning: {err or out}")

        # Clean up any conflicting XDG entry
        XDG_DESKTOP_PATH.unlink(missing_ok=True)
    else:
        # Fallback: Write XDG Desktop Autostart Entry only if systemctl --user is unavailable
        XDG_AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        desktop_contents = f"""[Desktop Entry]
Type=Application
Name=Dual Kiosk Auto-Reseat & Launch
Comment=Automatically reseats displays and USB devices and launches kiosks on startup
Exec="{target_script}"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
        try:
            XDG_DESKTOP_PATH.write_text(desktop_contents)
            logger.info(f"XDG autostart file written to {XDG_DESKTOP_PATH}")
        except Exception as exc:
            logger.warning(f"Failed to write XDG autostart desktop file: {exc}")
            return False

    # 4. Save state to config atomically under lock
    try:
        from core.config_manager import ConfigManager
        ConfigManager().update(lambda c: c.setdefault("settings", {}).__setitem__("auto_start_kiosks", True))
    except Exception as exc:
        logger.warning(f"Could not update config file for autostart: {exc}")

    global _autostart_cache
    _autostart_cache = None
    return True


def disable_autostart() -> bool:
    """
    Disables automatic launch on startup and cleans up services and autostart files.
    """
    global _autostart_cache
    _autostart_cache = None
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

    # Update config setting atomically under lock
    try:
        from core.config_manager import ConfigManager
        ConfigManager().update(lambda c: c.setdefault("settings", {}).__setitem__("auto_start_kiosks", False))
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
    Delegates to the central supervisor.
    """
    from core.supervisor import run
    return run(sleep_fn=sleep_fn, stop_event=stop_event)
