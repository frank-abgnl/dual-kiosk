#!/usr/bin/env python3
"""
Configuration Manager for XInput Pseudo-Seating.
Stores and validates JSON configurations at ~/.config/xinput-seats/config.json.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("xinput_seating.config")

CONFIG_DIR = Path.home() / ".config" / "xinput-seats"
CONFIG_FILE = CONFIG_DIR / "config.json"


class ConfigManager:
    """Handles loading, saving, validating, and auto-generating multiseat configurations."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self.config_dir = self.config_path.parent
        self.config_dir.mkdir(parents=True, exist_ok=True)

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
            return data
        except Exception as exc:
            logger.error(f"Error reading {self.config_path}: {exc}; falling back to default.")
            return self.get_default_config()

    def save_config(self, config_dict: Dict) -> bool:
        """Saves configuration dict to JSON file."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2)
            logger.info(f"Config successfully saved to {self.config_path}")
            return True
        except Exception as exc:
            logger.error(f"Failed to save config to {self.config_path}: {exc}")
            return False

    def auto_generate_config(self, device_manager) -> Dict:
        """
        Auto-populates configuration from current connected hardware.
        """
        auto_map = device_manager.auto_detect_seat_assignments()
        cfg = self.get_default_config()

        # Update Seat 1
        s1 = auto_map.get("seat1", {})
        cfg["seats"][0]["display"] = s1.get("display", "eDP-1")
        cfg["seats"][0]["mouse_id"] = s1.get("mouse_id")
        cfg["seats"][0]["mouse_name"] = s1.get("mouse_name", "")
        cfg["seats"][0]["keyboard_id"] = s1.get("keyboard_id")
        cfg["seats"][0]["keyboard_name"] = s1.get("keyboard_name", "")

        # Update Seat 2
        s2 = auto_map.get("seat2", {})
        cfg["seats"][1]["display"] = s2.get("display", "HDMI-1")
        cfg["seats"][1]["mouse_id"] = s2.get("mouse_id")
        cfg["seats"][1]["mouse_name"] = s2.get("mouse_name", "")
        cfg["seats"][1]["keyboard_id"] = s2.get("keyboard_id")
        cfg["seats"][1]["keyboard_name"] = s2.get("keyboard_name", "")

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
