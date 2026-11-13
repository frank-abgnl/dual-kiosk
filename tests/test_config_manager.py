import json
import threading
from pathlib import Path
import pytest
from core.config_manager import ConfigManager


def test_config_update_mutator(tmp_path):
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(cfg_file)

    def mutator(cfg):
        cfg["settings"]["custom_flag"] = 42

    updated = mgr.update(mutator)
    assert updated["settings"]["custom_flag"] == 42
    assert cfg_file.exists()

    # Re-reading should persist the change
    loaded = mgr.load_config()
    assert loaded["settings"]["custom_flag"] == 42


def test_config_backup_creation(tmp_path):
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(cfg_file)

    # First write creates initial config
    mgr.save_config({"version": "1.0", "seats": [], "settings": {"v": 1}})
    bak_file = tmp_path / "config.json.bak"
    assert not bak_file.exists()

    # Second write backs up the first write
    mgr.save_config({"version": "1.0", "seats": [], "settings": {"v": 2}})
    assert bak_file.exists()
    bak_data = json.loads(bak_file.read_text())
    assert bak_data["settings"]["v"] == 1


def test_corrupt_config_handling(tmp_path):
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(cfg_file)

    # Save initial config then update so a .bak is generated
    mgr.save_config({"version": "1.0", "seats": [], "settings": {"valid": True}})
    mgr.save_config({"version": "1.0", "seats": [], "settings": {"valid": True, "v": 2}})

    # Now write garbage into config.json
    cfg_file.write_text("{ this is corrupt json !!!")

    loaded = mgr.load_config()
    # It should recover from .bak
    assert loaded["settings"]["valid"] is True

    # Check that a corrupt file was created
    corrupt_files = list(tmp_path.glob("config.json.corrupt-*"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_text() == "{ this is corrupt json !!!"


def test_concurrent_updates(tmp_path):
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(cfg_file)

    # Initialize counter
    mgr.save_config({"version": "1.0", "seats": [], "settings": {"counter": 0}})

    def worker():
        for _ in range(15):
            mgr.update(lambda c: c["settings"].__setitem__("counter", c["settings"]["counter"] + 1))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = mgr.load_config()
    assert final["settings"]["counter"] == 60


def test_concurrent_updates_8_threads_different_keys(tmp_path):
    """Verify 8 concurrent threads each updating a different key leaves all 8 keys present."""
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(cfg_file)
    mgr.save_config({"version": "1.0", "seats": [], "settings": {}})

    def worker(key_idx):
        def mutator(c):
            c["settings"][f"thread_key_{key_idx}"] = f"val_{key_idx}"
        mgr.update(mutator)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = mgr.load_config()
    for i in range(8):
        assert final["settings"].get(f"thread_key_{i}") == f"val_{i}"


def test_config_migration(tmp_path):
    """Verify loading legacy config migrates volatile runtime keys to runtime.json."""
    cfg_file = tmp_path / "config.json"
    runtime_file = tmp_path / "runtime.json"
    legacy_cfg = {
        "seats": [
            {
                "id": "seat1",
                "mouse_id": 5,
                "keyboard_id": 6,
                "mouse_name": "Optical Mouse",
                "keyboard_name": "USB Keyboard",
                "display": "HDMI-1",
                "resolution": "1920x1080",
                "display_layout": "1920x1080+0+0",
            }
        ],
        "settings": {"auto_start": True},
    }
    cfg_file.write_text(json.dumps(legacy_cfg))

    from unittest.mock import patch
    with patch("core.paths.RUNTIME_FILE", runtime_file):
        mgr = ConfigManager(cfg_file)
        loaded = mgr.load_config(include_runtime=False)

        # Volatile IDs removed from config
        assert "mouse_id" not in loaded["seats"][0]
        assert "keyboard_id" not in loaded["seats"][0]
        assert "version" in loaded

        # Verify disk file contains no numeric device IDs
        disk_cfg = json.loads(cfg_file.read_text())
        assert "mouse_id" not in disk_cfg["seats"][0]
        assert "keyboard_id" not in disk_cfg["seats"][0]

        # Moved to runtime.json
        assert runtime_file.exists()
        rt = json.loads(runtime_file.read_text())
        assert rt["seats"]["seat1"]["mouse_id"] == 5
        assert rt["seats"]["seat1"]["keyboard_id"] == 6
