import pytest
from unittest.mock import MagicMock
from pathlib import Path

from core.kiosk_manager import should_reset, reset_profile_data, KioskManager


def test_should_reset_disabled():
    # 0 = off, negative = off
    assert should_reset(1000.0, 2000.0, 0) is False
    assert should_reset(1000.0, 2000.0, -5) is False


def test_should_reset_invalid_ts():
    assert should_reset(0.0, 1000.0, 5) is False
    assert should_reset(-1.0, 1000.0, 5) is False


def test_should_reset_threshold():
    last_input = 1000.0
    minutes = 5
    # threshold = 5 * 60 = 300 seconds

    # Not yet reached
    assert should_reset(last_input, last_input + 299.0, minutes) is False

    # Exact threshold reached
    assert should_reset(last_input, last_input + 300.0, minutes) is True

    # Exceeded
    assert should_reset(last_input, last_input + 600.0, minutes) is True


def test_should_reset_clock_skew():
    last_input = 1000.0
    now = 500.0  # Clock jumped backward
    assert should_reset(last_input, now, 5) is False


def test_reset_profile_data_preserves_user_js_and_policies(tmp_path):
    prof = tmp_path / "seat1"
    prof.mkdir()
    user_js = prof / "user.js"
    user_js.write_text('user_pref("kiosk", true);')

    dist = prof / "distribution"
    dist.mkdir()
    policies = dist / "policies.json"
    policies.write_text('{"policies": {}}')

    # Browsing artifacts that must be wiped
    cookies = prof / "cookies.sqlite"
    cookies.write_text("cookie_data")
    storage = prof / "storage"
    storage.mkdir()
    (storage / "test.db").write_text("db_data")

    reset_profile_data(prof)

    # user.js and distribution must remain intact
    assert user_js.exists()
    assert policies.exists()

    # Browser history/cookies must be wiped
    assert not cookies.exists()
    assert not storage.exists()


def test_check_idle_resets_triggers_seat_reset(monkeypatch):
    km = KioskManager()
    km.idle_reset_minutes = 5
    km.kiosk_configs = {
        "seat1": {"kiosk_url": "https://duckduckgo.com"},
        "seat2": {"kiosk_url": "https://www.google.com"},
    }

    mock_reset = MagicMock()
    monkeypatch.setattr(km, "reset_seat_kiosk", mock_reset)

    # seat1 idle for 6 minutes (360s), seat2 active 1 minute ago (60s)
    now = 1000.0
    km.last_input_ts["seat1"] = now - 360.0
    km.last_input_ts["seat2"] = now - 60.0

    km.check_idle_resets(now=now)

    mock_reset.assert_called_once_with("seat1")
