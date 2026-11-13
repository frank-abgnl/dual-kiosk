import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from core.kiosk_manager import (
    KioskManager,
    validate_kiosk_url,
    check_system_policies_installed,
)


def test_validate_kiosk_url_valid():
    assert validate_kiosk_url("https://duckduckgo.com") == "https://duckduckgo.com"
    assert validate_kiosk_url("http://localhost:8080/kiosk") == "http://localhost:8080/kiosk"
    assert validate_kiosk_url("about:blank") == "about:blank"


def test_validate_kiosk_url_invalid():
    with pytest.raises(ValueError):
        validate_kiosk_url("file:///etc/passwd")

    with pytest.raises(ValueError):
        validate_kiosk_url("javascript:alert(1)")

    with pytest.raises(ValueError):
        validate_kiosk_url("")

    with pytest.raises(ValueError):
        validate_kiosk_url(None)


def test_setup_profile_escaping_and_prefs(tmp_path, monkeypatch):
    monkeypatch.setattr("core.kiosk_manager.PROFILES_DIR", tmp_path)
    km = KioskManager()

    complex_url = 'https://example.com/search?q="test value"&lang=en'
    prof_path = km.setup_profile("seat1", kiosk_url=complex_url, profile_name="test_prof")

    assert prof_path == tmp_path / "test_prof"
    user_js = (prof_path / "user.js").read_text()

    # URL must be escaped with json.dumps
    expected_escaped = json.dumps(complex_url)
    assert f'user_pref("browser.startup.homepage", {expected_escaped});' in user_js

    # Required enterprise preferences
    assert 'user_pref("devtools.policy.disabled", true);' in user_js
    assert 'user_pref("browser.shell.checkDefaultBrowser", false);' in user_js
    assert 'user_pref("browser.tabs.warnOnClose", false);' in user_js
    assert 'user_pref("security.fileuri.strict_origin_policy", true);' in user_js


def test_lock_kiosk_false_produces_no_lock_window_call(tmp_path, monkeypatch):
    monkeypatch.setattr("core.kiosk_manager.PROFILES_DIR", tmp_path)
    km = KioskManager()

    # Mock subprocess.Popen so no real browser spawns
    fake_proc = MagicMock()
    fake_proc.pid = 99999
    fake_proc.poll.return_value = None
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: fake_proc)

    # Mock window lookup
    monkeypatch.setattr(km, "_find_window_id_for_pid", lambda pid: "0x4200001")
    mock_place = MagicMock()
    mock_lock = MagicMock()
    monkeypatch.setattr(km, "place_window", mock_place)
    monkeypatch.setattr(km, "lock_window", mock_lock)

    # Launch with enforce_lock=False
    ok = km.launch_kiosk(
        "seat1",
        kiosk_url="https://duckduckgo.com",
        display_geometry=(1920, 1080, 0, 0),
        enforce_lock=False,
    )
    assert ok is True

    # Directly run the window enforcement method synchronously to verify
    km._enforce_window_after_launch("seat1", fake_proc.pid, (1920, 1080, 0, 0), enforce_lock=False)

    mock_place.assert_called_with("0x4200001", (1920, 1080, 0, 0))
    mock_lock.assert_not_called()

    # Launch with enforce_lock=True
    km._enforce_window_after_launch("seat1", fake_proc.pid, (1920, 1080, 0, 0), enforce_lock=True)
    mock_lock.assert_called_once_with("0x4200001")


def test_system_policies_detection(tmp_path, monkeypatch):
    fake_policy = tmp_path / "policies.json"
    monkeypatch.setattr("core.kiosk_manager.SYSTEM_POLICY_PATHS", [fake_policy])

    assert check_system_policies_installed() is False
    fake_policy.write_text("{}")
    assert check_system_policies_installed() is True
