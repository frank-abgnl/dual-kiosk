#!/usr/bin/env python3
"""
Cross-process overlay whitelist.

The Control Board overlay may run in a different process than the kiosk watchdog /
shield (autostart or launcher vs. a spawned `--overlay` GUI). This file is the shared
signal: while a seat is listed here, the watchdog leaves that seat alone and the shield
lets the Control Board's windows (identified by pid / xid) stay above the kiosk.
Entries expire after TTL seconds unless refreshed (crash safety).
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from core.paths import STATE_DIR

STATE_FILE = STATE_DIR / "overlay_state.json"
TTL_SEC = 6.0
_lock = threading.Lock()


def _read() -> Dict[str, dict]:
    try:
        raw = json.loads(STATE_FILE.read_text())
    except Exception:
        return {}
    out = {}
    for seat, v in raw.items():
        if isinstance(v, dict):
            out[seat] = v
        else:  # legacy format: plain timestamp
            try:
                out[seat] = {"ts": float(v)}
            except (TypeError, ValueError):
                pass
    return out


def _write(data: dict):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def mark_active(seat_id: str, xid: Optional[int] = None, pid: Optional[int] = None):
    """Whitelist a seat (call again periodically as a heartbeat). pid defaults to this process."""
    with _lock:
        data = _read()
        old = data.get(seat_id, {})
        data[seat_id] = {
            "ts": time.time(),
            "pid": pid or old.get("pid") or os.getpid(),
            "xid": xid if xid is not None else old.get("xid"),
        }
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


def active_info() -> Dict[str, dict]:
    """{seat_id: {'pid':..., 'xid':...}} for every non-expired entry."""
    now = time.time()
    return {s: v for s, v in _read().items() if now - float(v.get("ts", 0)) < TTL_SEC}


def active_seats() -> List[str]:
    return list(active_info().keys())


def is_active(seat_id: Optional[str] = None) -> bool:
    seats = active_seats()
    return bool(seats) if seat_id is None else seat_id in seats
