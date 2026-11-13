import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.combo_hotkey import ComboTracker
from core.config_manager import ConfigManager
from core.display_guard import DisplayGuard
from core.kiosk_manager import KioskManager
from core.xinput_seating import XInputSeatingEngine


def test_persist_lock_4tuple_unpacking(tmp_path):
    """Verify _persist_lock unpacks KEYS[cls] 4-tuple and saves locked identity to config.json."""
    cfg_file = tmp_path / "config.json"
    cm = ConfigManager(config_path=cfg_file, runtime_path=tmp_path / "runtime.json")
    cm.save_config(cm.get_default_config())

    engine = XInputSeatingEngine()
    ident = {"name": "Logitech USB Mouse", "product": [1133, 49271], "serial": "ABC12345", "port": "1-2"}

    with patch("core.config_manager.ConfigManager", return_value=cm):
        engine._persist_lock("seat1", "pointer", "Logitech USB Mouse", ident)

    saved = cm.load_config()
    s1 = next(s for s in saved["seats"] if s["id"] == "seat1")
    assert s1["mouse_name"] == "Logitech USB Mouse"
    assert s1["mouse_ident"] == ident


def test_combo_tracker_cross_device_release_does_not_retrigger():
    """Verify releasing a key on device 2 while device 1 holds the combo does not reset device 1's hold state."""
    tracker = ComboTracker(required=("CTRL", "SHIFT", "M", "KEY"), debounce=0.0)
    t0 = 100.0
    assert tracker.event(1, "CTRL", True, now=t0) is False
    assert tracker.event(1, "SHIFT", True, now=t0) is False
    assert tracker.event(1, "M", True, now=t0) is False
    assert tracker.event(1, "KEY", True, now=t0) is True

    # Device 2 presses and releases unrelated key while Device 1 still holds combo
    assert tracker.event(2, None, False, now=t0 + 0.1) is False

    # Device 1 sends key-repeat while still held: must NOT fire a second time
    assert tracker.event(1, "KEY", True, now=t0 + 0.2) is False


def test_discover_running_kiosks_custom_profile(tmp_path, monkeypatch):
    """Verify discover_running_kiosks matches custom profile_name configured on a seat."""
    km = KioskManager()
    km.kiosk_configs["seat2"] = {"profile_name": "reception_desk_b"}

    fake_wmctrl = "0x0400000a  0 42424  host Mozilla Firefox\n"
    monkeypatch.setattr("core.kiosk_manager.run_command", lambda args, **kw: (fake_wmctrl, "", 0))

    orig_exists = Path.exists
    orig_read_bytes = Path.read_bytes

    def mock_exists(self):
        if str(self) == "/proc/42424/cmdline":
            return True
        return orig_exists(self)

    def mock_read_bytes(self):
        if str(self) == "/proc/42424/cmdline":
            return b"/usr/lib/firefox/firefox\x00--profile\x00/home/user/.config/xinput-seats/profiles/reception_desk_b\x00"
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "exists", mock_exists)
    monkeypatch.setattr(Path, "read_bytes", mock_read_bytes)

    discovered = km.discover_running_kiosks()
    assert "seat2" in discovered
    assert discovered["seat2"]["pid"] == 42424
    assert km.window_ids["seat2"] == "0x0400000a"


def test_display_guard_remembers_layout_in_runtime(tmp_path):
    """Verify DisplayGuard._remember_layout stores display_layout in runtime.json without dirtying config.json."""
    cfg_file = tmp_path / "config.json"
    rt_file = tmp_path / "runtime.json"
    cm = ConfigManager(config_path=cfg_file, runtime_path=rt_file)
    cm.save_config(cm.get_default_config())

    km = KioskManager()
    dg = DisplayGuard(km)
    dg.cfg_mgr = cm

    seats = [{"id": "seat1", "display": "eDP-1"}, {"id": "seat2", "display": "HDMI-1"}]
    displays = [
        {"name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080},
        {"name": "HDMI-1", "x": 1920, "y": 0, "width": 1920, "height": 1080},
    ]
    by_name = {d["name"]: d for d in displays}

    dg._remember_layout(seats, displays, by_name)

    rt = cm.load_runtime()
    assert "display_layout" in rt
    assert rt["display_layout"]["HDMI-1"]["x"] == 1920
    on_disk_cfg = json.loads(cfg_file.read_text())
    assert "display_layout" not in on_disk_cfg.get("settings", {})
