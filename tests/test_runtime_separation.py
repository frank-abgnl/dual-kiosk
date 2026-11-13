import json
from pathlib import Path
import pytest
from core.config_manager import ConfigManager, VOLATILE_SEAT_KEYS, VOLATILE_ROOT_KEYS


def test_migrate_separates_volatile_keys():
    old_cfg = {
        "display_layout": {"primary": "HDMI-1", "mode": "dual"},
        "seats": [
            {
                "id": "seat1",
                "name": "Seat 1",
                "display": "HDMI-1",
                "resolution": "1920x1080",
                "width": 1920,
                "height": 1080,
                "mouse_id": 10,
                "mouse_name": "Optical Mouse",
                "mouse_connected": True,
                "keyboard_id": 11,
                "keyboard_name": "Gaming Keyboard",
                "keyboard_connected": True,
                "kiosk_url": "https://duckduckgo.com",
            },
            {
                "id": "seat2",
                "name": "Seat 2",
                "display": "DP-1",
                "resolution": "2560x1440",
                "width": 2560,
                "height": 1440,
                "mouse_id": 12,
                "mouse_name": "Wireless Mouse",
                "mouse_connected": False,
                "keyboard_id": 13,
                "keyboard_name": "Standard Keyboard",
                "keyboard_connected": True,
                "kiosk_url": "https://www.google.com",
            },
        ],
        "settings": {"auto_start_kiosks": True},
    }

    clean_cfg, runtime_dict = ConfigManager.migrate(old_cfg)

    # Clean config must have version
    assert clean_cfg["version"] == "1.0"
    # display_layout must be moved from clean_cfg to runtime_dict
    assert "display_layout" not in clean_cfg
    assert runtime_dict["display_layout"] == {"primary": "HDMI-1", "mode": "dual"}

    # Seat 1 checks
    s1_clean = clean_cfg["seats"][0]
    for vk in ("mouse_id", "keyboard_id", "resolution", "width", "height", "mouse_connected", "keyboard_connected"):
        assert vk not in s1_clean
    assert s1_clean["mouse_name"] == "Optical Mouse"
    assert s1_clean["kiosk_url"] == "https://duckduckgo.com"

    s1_runtime = runtime_dict["seats"]["seat1"]
    assert s1_runtime["mouse_id"] == 10
    assert s1_runtime["keyboard_id"] == 11
    assert s1_runtime["resolution"] == "1920x1080"
    assert s1_runtime["mouse_connected"] is True
    assert s1_runtime["keyboard_connected"] is True

    # Seat 2 checks
    s2_clean = clean_cfg["seats"][1]
    for vk in ("mouse_id", "keyboard_id", "resolution", "width", "height", "mouse_connected", "keyboard_connected"):
        assert vk not in s2_clean
    assert s2_clean["mouse_name"] == "Wireless Mouse"

    s2_runtime = runtime_dict["seats"]["seat2"]
    assert s2_runtime["mouse_id"] == 12
    assert s2_runtime["keyboard_id"] == 13
    assert s2_runtime["resolution"] == "2560x1440"
    assert s2_runtime["mouse_connected"] is False


def test_runtime_file_persistence_and_merge(tmp_path):
    cfg_file = tmp_path / "config.json"
    runtime_file = tmp_path / "runtime.json"
    mgr = ConfigManager(config_path=cfg_file, runtime_path=runtime_file)

    raw_cfg = {
        "version": "1.0",
        "seats": [
            {
                "id": "seat1",
                "display": "HDMI-1",
                "mouse_id": 42,
                "mouse_name": "Test Mouse",
                "keyboard_id": 43,
                "keyboard_name": "Test Kbd",
            }
        ],
        "settings": {},
    }

    # Saving config separates runtime state into runtime_file
    mgr.save_config(raw_cfg)

    # config.json should NOT contain mouse_id or keyboard_id
    on_disk_cfg = json.loads(cfg_file.read_text())
    assert "mouse_id" not in on_disk_cfg["seats"][0]
    assert "keyboard_id" not in on_disk_cfg["seats"][0]

    # runtime.json MUST contain mouse_id and keyboard_id
    assert runtime_file.exists()
    on_disk_runtime = json.loads(runtime_file.read_text())
    assert on_disk_runtime["seats"]["seat1"]["mouse_id"] == 42
    assert on_disk_runtime["seats"]["seat1"]["keyboard_id"] == 43

    # load_config(include_runtime=True) merges them back seamlessly
    loaded_merged = mgr.load_config(include_runtime=True)
    assert loaded_merged["seats"][0]["mouse_id"] == 42
    assert loaded_merged["seats"][0]["keyboard_id"] == 43

    # load_config(include_runtime=False) gives clean config
    loaded_clean = mgr.load_config(include_runtime=False)
    assert "mouse_id" not in loaded_clean["seats"][0]

    # update_runtime modifies runtime state under lock
    mgr.update_runtime(lambda r: r["seats"]["seat1"].__setitem__("mouse_id", 99))
    updated_merged = mgr.load_config(include_runtime=True)
    assert updated_merged["seats"][0]["mouse_id"] == 99
