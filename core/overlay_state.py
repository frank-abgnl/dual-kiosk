#!/usr/bin/env python3
"""
Cross-process overlay whitelist.

The Control Board overlay may run in a different process than the kiosk watchdog
(autostart / --start CLI vs. a spawned `--overlay` GUI). This file is the shared
signal: while a seat is listed here, the watchdog leaves that seat alone.
Entries expire after TTL seconds unless refreshed (crash safety).
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

STATE_FILE = Path.home() / ".config" / "xinput-seats" / "overlay_state.json"
TTL_SEC = 6.0
_lock = threading.Lock()


def _read() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _write(data: dict):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def mark_active(seat_id: str):
    """Whitelist a seat (call again periodically as a heartbeat)."""
    with _lock:
        data = _read()
        data[seat_id] = time.time()
        _write(data)


def clear(seat_id: Optional[str] = None):
    """Remove one seat (or all seats) from the whitelist."""
    with _lock:
        if seat_id is None:
            _write({})
            return
        data = _read()
        data.pop(seat_id, None)
        _write(data)


def active_seats() -> List[str]:
    now = time.time()
    data = _read()
    return [s for s, ts in data.items() if now - float(ts) < TTL_SEC]


def is_active(seat_id: Optional[str] = None) -> bool:
    seats = active_seats()
    return bool(seats) if seat_id is None else seat_id in seats
