#!/usr/bin/env python3
"""
Configuration Manager for XInput Pseudo-Seating.
Stores and validates JSON configurations.
"""

import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("xinput_seating.config")

from core.paths import CONFIG_DIR, CONFIG_FILE


class ConfigManager:
    """Handles loading, saving, validating, and auto-generating multiseat configurations."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self.config_dir = self.config_path.parent
        self.lock_path = self.config_dir / "config.lock"

    def get_default_config(self) -> Dict:
        """Returns the default configuration template."""
        return {
            "version": "1.0",
            "seats": [
                {
                    "id": "seat1",
                    "name": "Seat 1 (Primary)",
                    "is_primary": True,
                    "display": "eDP-1",
                    "mouse_id": None,
                    "mouse_name": "",
                    "keyboard_id": None,
                    "keyboard_name": "",
                    "lock_peripherals": True,
                    "locked_display": "eDP-1",
                    "profile_name": "seat1",
                    "kiosk_url": "https://duckduckgo.com",
                    "lock_kiosk": True,
                    "auto_respawn": True,
                },
                {
                    "id": "seat2",
                    "name": "Seat 2 (Secondary)",
                    "is_primary": False,
                    "display": "HDMI-1",
                    "mouse_id": None,
                    "mouse_name": "",
                    "keyboard_id": None,
                    "keyboard_name": "",
                    "lock_peripherals": True,
                    "locked_display": "HDMI-1",
                    "profile_name": "seat2",
                    "kiosk_url": "https://www.google.com",
                    "lock_kiosk": True,
                    "auto_respawn": True,
                },
            ],
            "settings": {
                "auto_start_kiosks": True,
                "confine_mouse_to_screen": True,
                "watchdog_interval_sec": 1.5,
                "lock_peripherals_to_displays": True,
                "hotplug_auto_reseat": True,
            },
        }

    def load_config(self) -> Dict:
        """Loads configuration from JSON file or returns default if not present."""
        if not self.config_path.exists():
            logger.info(f"Config file not found at {self.config_path}; generating default.")
            cfg = self.get_default_config()
            self.save_config(cfg)
            return cfg

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Config root must be a JSON object")
            return data
        except (json.JSONDecodeError, ValueError) as exc:
            ts = int(time.time())
            corrupt_path = self.config_dir / f"config.json.corrupt-{ts}"
            logger.error(f"Corrupt config at {self.config_path}: {exc}. Moving to {corrupt_path}")
            try:
                shutil.move(str(self.config_path), str(corrupt_path))
            except Exception as move_exc:
                logger.warning(f"Could not move corrupt config: {move_exc}")

            # Try loading from backup
            bak_path = self.config_path.with_name(self.config_path.name + ".bak")
            if bak_path.exists():
                try:
                    with open(bak_path, "r", encoding="utf-8") as f:
                        bak_data = json.load(f)
                    if isinstance(bak_data, dict):
                        logger.info(f"Recovered configuration from {bak_path}")
                        self.save_config(bak_data)
                        return bak_data
                except Exception as bak_exc:
                    logger.warning(f"Could not load backup config {bak_path}: {bak_exc}")

            logger.info("Falling back to default configuration.")
            cfg = self.get_default_config()
            self.save_config(cfg)
            return cfg
        except Exception as exc:
            logger.error(f"Error reading {self.config_path}: {exc}; falling back to default.")
            return self.get_default_config()

    def save_config(self, config_dict: Dict) -> bool:
        """Saves configuration dict atomically to JSON file with backup."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            # Create backup of current config if it exists
            bak_path = self.config_path.with_name(self.config_path.name + ".bak")
            if self.config_path.exists():
                try:
                    shutil.copy2(str(self.config_path), str(bak_path))
                except Exception as bak_exc:
                    logger.debug(f"Could not create config backup: {bak_exc}")

            tmp_path = self.config_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2)
            os.replace(tmp_path, self.config_path)
            logger.info(f"Config successfully saved to {self.config_path}")
            return True
        except Exception as exc:
            logger.error(f"Failed to save config to {self.config_path}: {exc}")
            return False

    def update(self, mutator: Callable[[Dict], None]) -> Dict:
        """
        Thread-safe and process-safe read-modify-write on configuration.
        Acquires exclusive flock on config.lock, loads current config,
        applies mutator(cfg), saves the result atomically, and returns the updated config.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                cfg = self.load_config()
                mutator(cfg)
                self.save_config(cfg)
                return cfg
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

    def auto_generate_config(self, device_manager, force_reassign: bool = False) -> Dict:
        """
        Auto-populates configuration from current connected hardware,
        identifying screen resolutions and connected USB input devices.
        Preserves custom URLs and kiosk preferences if config already exists.
        Retains memory of locked peripheral names to displays across replugs and reboots.
        """
        existing_cfg = self.load_config() if self.config_path.exists() else {}
        existing_seats = existing_cfg.get("seats") if not force_reassign else None

        auto_map = device_manager.auto_detect_seat_assignments(existing_seats=existing_seats)
        cfg = existing_cfg if existing_cfg.get("seats") else self.get_default_config()
        if not cfg.get("seats") or len(cfg.get("seats", [])) < 2:
            cfg = self.get_default_config()

        # Update Seat 1
        s1 = auto_map.get("seat1", {})
        cfg["seats"][0]["display"] = s1.get("display", "eDP-1")
        cfg["seats"][0]["resolution"] = s1.get("resolution", "1920x1080")
        cfg["seats"][0]["mouse_id"] = s1.get("mouse_id")
        cfg["seats"][0]["mouse_name"] = s1.get("mouse_name", "")
        cfg["seats"][0]["keyboard_id"] = s1.get("keyboard_id")
        cfg["seats"][0]["keyboard_name"] = s1.get("keyboard_name", "")
        cfg["seats"][0]["lock_peripherals"] = s1.get("lock_peripherals", True)
        cfg["seats"][0]["locked_display"] = s1.get("locked_display", cfg["seats"][0]["display"])

        # Update Seat 2
        s2 = auto_map.get("seat2", {})
        cfg["seats"][1]["display"] = s2.get("display", "HDMI-1")
        cfg["seats"][1]["resolution"] = s2.get("resolution", "1920x1080")
        cfg["seats"][1]["mouse_id"] = s2.get("mouse_id")
        cfg["seats"][1]["mouse_name"] = s2.get("mouse_name", "")
        cfg["seats"][1]["keyboard_id"] = s2.get("keyboard_id")
        cfg["seats"][1]["keyboard_name"] = s2.get("keyboard_name", "")
        cfg["seats"][1]["lock_peripherals"] = s2.get("lock_peripherals", True)
        cfg["seats"][1]["locked_display"] = s2.get("locked_display", cfg["seats"][1]["display"])

        self.save_config(cfg)
        return cfg

    def validate_config(self, cfg: Dict) -> List[str]:
        """Validates configuration and returns a list of issues (empty if valid)."""
        issues = []
        if not isinstance(cfg, dict):
            return ["Root config must be a JSON object"]

        seats = cfg.get("seats", [])
        if not isinstance(seats, list) or len(seats) < 2:
            issues.append("Config should have at least 2 seats defined.")

        for idx, seat in enumerate(seats):
            sid = seat.get("id", f"Seat #{idx+1}")
            if not seat.get("display"):
                issues.append(f"[{sid}] Missing target display.")
            if seat.get("mouse_id") is None:
                issues.append(f"[{sid}] No mouse ID assigned.")
            if seat.get("keyboard_id") is None:
                issues.append(f"[{sid}] No keyboard ID assigned.")

        return issues
