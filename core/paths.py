#!/usr/bin/env python3
"""
Central path definitions for xinput-seats configuration and runtime state.
"""
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("xinput_seating.paths")

CONFIG_DIR = Path.home() / ".config" / "xinput-seats"
_xdg_state = os.environ.get("XDG_STATE_HOME")
STATE_DIR = (Path(_xdg_state) / "xinput-seats") if _xdg_state else (Path.home() / ".local" / "state" / "xinput-seats")

CONFIG_FILE = CONFIG_DIR / "config.json"
RUNTIME_FILE = STATE_DIR / "runtime.json"
LOCK_FILE = STATE_DIR / "launcher.lock"
PID_FILE = STATE_DIR / "launcher.pid"
LOG_FILE = STATE_DIR / "launcher.log"
SOCKET_PATH = STATE_DIR / "overlay.sock"
SPAWN_GUARD_FILE = STATE_DIR / "spawn_guard"
KEYMAP_DIR = STATE_DIR / "keymaps"
PROFILES_DIR = CONFIG_DIR / "profiles"


def ensure_config_dir() -> Path:
    """Ensure CONFIG_DIR exists and return it."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def ensure_state_dir() -> Path:
    """Ensure STATE_DIR exists, perform one-time migration if needed, and return it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    migrate_old_state_files()
    return STATE_DIR


def migrate_old_state_files() -> None:
    """
    One-time migration: move legacy state files from CONFIG_DIR to STATE_DIR if they exist.
    Moves peripheral_state.json, overlay_state.json, launcher.* and keymaps/.
    """
    if not CONFIG_DIR.exists():
        return

    # Direct files
    targets = ["peripheral_state.json", "overlay_state.json"]
    for name in targets:
        old_file = CONFIG_DIR / name
        new_file = STATE_DIR / name
        if old_file.exists() and not new_file.exists():
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_file), str(new_file))
                logger.info(f"Migrated {old_file} -> {new_file}")
            except Exception as err:
                logger.warning(f"Could not migrate {old_file} to {new_file}: {err}")

    # launcher.* files (e.g. launcher.lock, launcher.pid, launcher.log)
    for old_file in CONFIG_DIR.glob("launcher.*"):
        new_file = STATE_DIR / old_file.name
        if old_file.exists() and not new_file.exists():
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_file), str(new_file))
                logger.info(f"Migrated {old_file} -> {new_file}")
            except Exception as err:
                logger.warning(f"Could not migrate {old_file} to {new_file}: {err}")

    # keymaps directory if previously in CONFIG_DIR
    old_keymaps = CONFIG_DIR / "keymaps"
    if old_keymaps.exists() and not KEYMAP_DIR.exists():
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_keymaps), str(KEYMAP_DIR))
            logger.info(f"Migrated {old_keymaps} -> {KEYMAP_DIR}")
        except Exception as err:
            logger.warning(f"Could not migrate {old_keymaps} to {KEYMAP_DIR}: {err}")
