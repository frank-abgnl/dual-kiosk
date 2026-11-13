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
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("xinput_seating.config")

from core.paths import CONFIG_DIR, CONFIG_FILE, RUNTIME_FILE

VOLATILE_SEAT_KEYS = {
    "mouse_id",
    "keyboard_id",
    "mouse_connected",
    "keyboard_connected",
    "pointer_connected",
    "resolution",
    "width",
    "height",
}

VOLATILE_ROOT_KEYS = {
    "display_layout",
}


class ConfigManager:
    """Handles loading, saving, validating, auto-generating, and runtime state separation."""

    def __init__(self, config_path: Optional[Path] = None, runtime_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self.config_dir = self.config_path.parent
        self.lock_path = self.config_dir / "config.lock"
        self.runtime_path = runtime_path or (self.config_dir / "runtime.json" if config_path else RUNTIME_FILE)

    @classmethod
    def migrate(cls, cfg: Dict) -> Tuple[Dict, Dict]:
        """
        Migrates old configuration format:
        Moves volatile runtime keys (mouse_id, keyboard_id, *_connected, display_layout, resolution)
        to a runtime dictionary, strips them from the config dictionary, and ensures version is set.
        Returns (cleaned_config, runtime_dict).
        """
        clean_cfg = json.loads(json.dumps(cfg)) if isinstance(cfg, dict) else {}
        clean_cfg["version"] = clean_cfg.get("version", "1.0")

        runtime_dict: Dict = {"seats": {}}

        # Move root volatile keys
        for k in list(clean_cfg.keys()):
            if k in VOLATILE_ROOT_KEYS:
                runtime_dict[k] = clean_cfg.pop(k)

        # Move seat volatile keys
        if "seats" in clean_cfg and isinstance(clean_cfg["seats"], list):
            for seat in clean_cfg["seats"]:
                if not isinstance(seat, dict):
                    continue
                sid = seat.get("id", "unknown")
                seat_runtime = {}
                for vk in list(seat.keys()):
                    if vk in VOLATILE_SEAT_KEYS or vk.endswith("_connected"):
                        seat_runtime[vk] = seat.pop(vk)
                if seat_runtime:
                    runtime_dict["seats"][sid] = seat_runtime

        return clean_cfg, runtime_dict

    def load_runtime(self) -> Dict:
        """Loads volatile runtime state from runtime_path."""
        if not self.runtime_path.exists():
            return {"seats": {}}
        try:
            with open(self.runtime_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"seats": {}}
        except Exception as exc:
            logger.debug(f"Failed to load runtime state from {self.runtime_path}: {exc}")
            return {"seats": {}}

    def save_runtime(self, runtime_dict: Dict) -> bool:
        """Saves runtime state atomically to runtime_path."""
        try:
            self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.runtime_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(runtime_dict, f, indent=2)
            os.replace(tmp_path, self.runtime_path)
            return True
        except Exception as exc:
            logger.error(f"Failed to save runtime state to {self.runtime_path}: {exc}")
            return False

    def update_runtime(self, mutator: Callable[[Dict], None]) -> Dict:
        """
        Thread-safe and process-safe read-modify-write on runtime state.
        Acquires exclusive flock on runtime.lock.
        """
        self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.runtime_path.parent / "runtime.lock"
        with open(lock_file, "a+", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                state = self.load_runtime()
                mutator(state)
                self.save_runtime(state)
                return state
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

    def _merge_runtime(self, cfg: Dict) -> Dict:
        """Merges volatile runtime values into a copy of cfg without altering disk config."""
        res = json.loads(json.dumps(cfg))
        runtime = self.load_runtime()
        for k in VOLATILE_ROOT_KEYS:
            if k in runtime:
                res[k] = runtime[k]
        seat_runtimes = runtime.get("seats", {})
        for s in res.get("seats", []):
            sid = s.get("id")
            if sid in seat_runtimes:
                s.update(seat_runtimes[sid])
        return res

    def get_default_config(self) -> Dict:
        """Returns the default configuration template from core.schema."""
        from core.schema import get_default_config as schema_default
        return schema_default()

    def reset_to_defaults(self) -> Dict:
        """
        Resets configuration to factory defaults.
        Guarantees that a backup (.bak) of the current configuration is written first.
        """
        if self.config_path.exists():
            bak_path = self.config_path.with_name(self.config_path.name + ".bak")
            try:
                shutil.copy2(str(self.config_path), str(bak_path))
                logger.info(f"Backed up config to {bak_path} before resetting to defaults.")
            except Exception as exc:
                logger.warning(f"Could not create backup before reset: {exc}")

        default_cfg = self.get_default_config()
        self.save_config(default_cfg)
        return default_cfg

    def load_config(self, include_runtime: bool = True) -> Dict:
        """Loads configuration from JSON file or returns default if not present."""
        if not self.config_path.exists():
            logger.info(f"Config file not found at {self.config_path}; generating default.")
            cfg = self.get_default_config()
            self.save_config(cfg)
            return self._merge_runtime(cfg) if include_runtime else cfg

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Config root must be a JSON object")

            has_volatile = any(
                any(vk in s for vk in VOLATILE_SEAT_KEYS)
                for s in data.get("seats", [])
                if isinstance(s, dict)
            ) or any(k in VOLATILE_ROOT_KEYS for k in data)

            if has_volatile or "version" not in data:
                cleaned_cfg, runtime_state = self.migrate(data)
                if runtime_state.get("seats") or any(k in runtime_state for k in VOLATILE_ROOT_KEYS):
                    self.update_runtime(lambda r: r.update(runtime_state))
                self.save_config(cleaned_cfg)
                data = cleaned_cfg

            return self._merge_runtime(data) if include_runtime else data
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
                        return self._merge_runtime(bak_data) if include_runtime else bak_data
                except Exception as bak_exc:
                    logger.warning(f"Could not load backup config {bak_path}: {bak_exc}")

            logger.info("Falling back to default configuration.")
            cfg = self.get_default_config()
            self.save_config(cfg)
            return self._merge_runtime(cfg) if include_runtime else cfg
        except Exception as exc:
            logger.error(f"Error reading {self.config_path}: {exc}; falling back to default.")
            return self.get_default_config()

    def save_config(self, config_dict: Dict) -> bool:
        """Saves configuration dict atomically to JSON file with backup and runtime separation."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            clean_cfg, runtime_state = self.migrate(config_dict)
            if runtime_state.get("seats") or any(k in runtime_state for k in VOLATILE_ROOT_KEYS):
                self.update_runtime(lambda r: r.update(runtime_state))

            # Create backup of current config if it exists
            bak_path = self.config_path.with_name(self.config_path.name + ".bak")
            if self.config_path.exists():
                try:
                    shutil.copy2(str(self.config_path), str(bak_path))
                except Exception as bak_exc:
                    logger.debug(f"Could not create config backup: {bak_exc}")

            tmp_path = self.config_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(clean_cfg, f, indent=2)
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
        """Validates configuration and returns a list of error strings."""
        from core.schema import validate
        return [str(err) for err in validate(cfg)]
