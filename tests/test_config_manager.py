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
