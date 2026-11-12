import shutil
from pathlib import Path
from unittest.mock import patch

from core import paths


def test_paths_defined():
    assert paths.CONFIG_DIR.name == "xinput-seats"
    assert paths.STATE_DIR.name == "xinput-seats"
    assert paths.CONFIG_FILE == paths.CONFIG_DIR / "config.json"
    assert paths.RUNTIME_FILE == paths.STATE_DIR / "runtime.json"
    assert paths.LOCK_FILE == paths.STATE_DIR / "launcher.lock"
    assert paths.PID_FILE == paths.STATE_DIR / "launcher.pid"
    assert paths.SOCKET_PATH == paths.STATE_DIR / "overlay.sock"
    assert paths.KEYMAP_DIR == paths.STATE_DIR / "keymaps"
    assert paths.PROFILES_DIR == paths.CONFIG_DIR / "profiles"


def test_migration(tmp_path):
    mock_config = tmp_path / "config"
    mock_state = tmp_path / "state"
    mock_config.mkdir()

    (mock_config / "peripheral_state.json").write_text('{"dummy": 1}')
    (mock_config / "overlay_state.json").write_text('{"dummy": 2}')
    (mock_config / "launcher.lock").write_text('lock')
    (mock_config / "launcher.pid").write_text('1234')

    with patch.object(paths, "CONFIG_DIR", mock_config), patch.object(paths, "STATE_DIR", mock_state):
        paths.migrate_old_state_files()

    assert (mock_state / "peripheral_state.json").exists()
    assert (mock_state / "overlay_state.json").exists()
    assert (mock_state / "launcher.lock").exists()
    assert (mock_state / "launcher.pid").exists()
    assert not (mock_config / "peripheral_state.json").exists()
