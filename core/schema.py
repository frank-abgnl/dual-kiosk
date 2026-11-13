"""
Configuration Schema Definition and Validation for XInput Pseudo-Seating.
Defines version constants, default configuration structure, and structured validation.
"""
from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional

CONFIG_VERSION = "1.0"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "auto_start_kiosks": True,
    "confine_mouse_to_screen": True,
    "watchdog_interval_sec": 1.5,
    "lock_peripherals_to_displays": True,
    "hotplug_auto_reseat": True,
    "idle_reset_minutes": 0,
}

DEFAULT_SEATS: List[Dict[str, Any]] = [
    {
        "id": "seat1",
        "name": "Seat 1 (Primary)",
        "is_primary": True,
        "display": "eDP-1",
        "mouse_id": None,
        "mouse_name": "",
        "mouse_port": None,
        "keyboard_id": None,
        "keyboard_name": "",
        "keyboard_port": None,
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
        "mouse_port": None,
        "keyboard_id": None,
        "keyboard_name": "",
        "keyboard_port": None,
        "lock_peripherals": True,
        "locked_display": "HDMI-1",
        "profile_name": "seat2",
        "kiosk_url": "https://www.google.com",
        "lock_kiosk": True,
        "auto_respawn": True,
    },
]


def get_default_config() -> Dict[str, Any]:
    """Returns the factory default configuration dictionary."""
    return {
        "version": CONFIG_VERSION,
        "seats": [dict(s) for s in DEFAULT_SEATS],
        "settings": dict(DEFAULT_SETTINGS),
    }


class SchemaError(NamedTuple):
    path: str
    message: str
    severity: str = "error"  # "error" or "warning"

    def __str__(self) -> str:
        return f"[{self.path}] {self.message}"


def validate(cfg: Any) -> List[SchemaError]:
    """
    Validates a configuration dictionary against the pseudo-seating schema.
    Returns a list of SchemaError items. Empty list means validation passed.
    """
    errors: List[SchemaError] = []
    if not isinstance(cfg, dict):
        errors.append(SchemaError(path="root", message="Configuration root must be a JSON object"))
        return errors

    # 1. Version check
    ver = cfg.get("version")
    if ver is None:
        errors.append(SchemaError(path="version", message="Missing 'version' field in config", severity="warning"))
    elif not isinstance(ver, str):
        errors.append(SchemaError(path="version", message="'version' field must be a string"))

    # 2. Seats check
    seats = cfg.get("seats")
    if seats is None:
        errors.append(SchemaError(path="seats", message="Missing 'seats' list in config"))
    elif not isinstance(seats, list):
        errors.append(SchemaError(path="seats", message="'seats' must be a list"))
    else:
        if len(seats) < 2:
            errors.append(SchemaError(path="seats", message=f"Expected at least 2 seats, found {len(seats)}", severity="warning"))

        seat_ids = set()
        primary_count = 0
        for idx, seat in enumerate(seats):
            spath = f"seats[{idx}]"
            if not isinstance(seat, dict):
                errors.append(SchemaError(path=spath, message="Seat configuration must be a JSON object"))
                continue

            sid = seat.get("id")
            if not sid or not isinstance(sid, str):
                errors.append(SchemaError(path=f"{spath}.id", message="Seat must have a non-empty string 'id'"))
            elif sid in seat_ids:
                errors.append(SchemaError(path=f"{spath}.id", message=f"Duplicate seat id '{sid}'"))
            else:
                seat_ids.add(sid)

            if seat.get("is_primary") is True:
                primary_count += 1

            # Validate kiosk_url
            url = seat.get("kiosk_url")
            if url is not None:
                if not isinstance(url, str):
                    errors.append(SchemaError(path=f"{spath}.kiosk_url", message="kiosk_url must be a string"))
                elif url and not (url.startswith("http://") or url.startswith("https://") or url.startswith("about:")):
                    errors.append(SchemaError(path=f"{spath}.kiosk_url", message=f"Invalid URL '{url}': must start with http://, https://, or about:"))

            # Validate boolean flags
            for bfield in ("lock_kiosk", "auto_respawn", "lock_peripherals", "is_primary"):
                val = seat.get(bfield)
                if val is not None and not isinstance(val, bool):
                    errors.append(SchemaError(path=f"{spath}.{bfield}", message=f"'{bfield}' must be a boolean (true/false)"))

            # Validate device IDs if set
            for id_field in ("mouse_id", "keyboard_id"):
                val = seat.get(id_field)
                if val is not None and not isinstance(val, int):
                    errors.append(SchemaError(path=f"{spath}.{id_field}", message=f"'{id_field}' must be an integer ID or null"))

        if primary_count == 0:
            errors.append(SchemaError(path="seats", message="At least one seat must have 'is_primary': true", severity="warning"))
        elif primary_count > 1:
            errors.append(SchemaError(path="seats", message=f"Found {primary_count} primary seats; only one seat should be primary", severity="warning"))

    # 3. Settings check
    settings = cfg.get("settings")
    if settings is not None:
        if not isinstance(settings, dict):
            errors.append(SchemaError(path="settings", message="'settings' must be a JSON object"))
        else:
            for bfield in ("auto_start_kiosks", "confine_mouse_to_screen", "lock_peripherals_to_displays", "hotplug_auto_reseat"):
                val = settings.get(bfield)
                if val is not None and not isinstance(val, bool):
                    errors.append(SchemaError(path=f"settings.{bfield}", message=f"'{bfield}' must be a boolean"))

            for num_field in ("watchdog_interval_sec", "idle_reset_minutes"):
                val = settings.get(num_field)
                if val is not None and not isinstance(val, (int, float)):
                    errors.append(SchemaError(path=f"settings.{num_field}", message=f"'{num_field}' must be a number"))
                elif val is not None and val < 0:
                    errors.append(SchemaError(path=f"settings.{num_field}", message=f"'{num_field}' cannot be negative"))

    return errors
