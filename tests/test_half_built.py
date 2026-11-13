import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_DIR = Path(__file__).resolve().parent.parent


def test_install_launcher_substitutes_app_dir(tmp_path):
    menu_dir = tmp_path / "applications"
    desk_dir = tmp_path / "desktop"
    menu_dir.mkdir(parents=True)
    desk_dir.mkdir(parents=True)

    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(tmp_path)
    # Mock xdg-user-dir
    script = PROJECT_DIR / "install-launcher.sh"

    res = subprocess.run(
        ["bash", str(script)],
        env=env,
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    multiseat_desktop = menu_dir / "xinput-multiseat.desktop"
    launcher_desktop = menu_dir / "xinput-kiosk-launcher.desktop"

    assert multiseat_desktop.exists()
    assert launcher_desktop.exists()

    content_multi = multiseat_desktop.read_text()
    content_launch = launcher_desktop.read_text()

    # Neither desktop file should contain the template placeholder @APP_DIR@
    assert "@APP_DIR@" not in content_multi
    assert "@APP_DIR@" not in content_launch
    assert str(PROJECT_DIR) in content_multi
    assert str(PROJECT_DIR) in content_launch


def test_device_finder_maps_xinput_id_and_assigns(tmp_path):
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    from gui.tabs.devices_tab import DeviceFinderDialog
    from core.config_manager import ConfigManager

    # Mock App and DeviceManager
    mock_app = MagicMock()
    fake_ptrs = [{"id": 14, "name": "Logitech USB Optical Mouse", "is_xtest": False}]
    fake_kbds = [{"id": 15, "name": "Dell USB Keyboard", "is_xtest": False}]
    mock_app.device_mgr.get_xinput_hierarchy.return_value = ({}, fake_ptrs, fake_kbds)

    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    mock_app.config_mgr = cfg_mgr

    # Initialize dialog (do not call run() to prevent blocking UI loop)
    dlg = DeviceFinderDialog(mock_app)

    # 1. Test mapping from name to xinput slave ID
    ptr_id = dlg._find_xinput_id("Logitech USB Optical Mouse", "pointer")
    assert ptr_id == 14

    kbd_id = dlg._find_xinput_id("Dell USB Keyboard", "keyboard")
    assert kbd_id == 15

    # 2. Test detected pointer and assignment to seat 2
    dlg._set_detected("Logitech USB Optical Mouse", "Pointer / Mouse", "pointer")
    assert dlg.detected_xinput_id == 14

    # Trigger assign to seat 2 (response 102)
    dlg._on_response(dlg, 102)

    saved_cfg = cfg_mgr.load_config()
    seat2 = next(s for s in saved_cfg["seats"] if s["id"] == "seat2")
    assert seat2["mouse_name"] == "Logitech USB Optical Mouse"
    assert seat2["mouse_id"] == 14

    # 3. Test detected keyboard and assignment to seat 1
    dlg._set_detected("Dell USB Keyboard", "Keyboard", "keyboard")
    assert dlg.detected_xinput_id == 15

    # Trigger assign to seat 1 (response 101)
    dlg._on_response(dlg, 101)

    saved_cfg = cfg_mgr.load_config()
    seat1 = next(s for s in saved_cfg["seats"] if s["id"] == "seat1")
    assert seat1["keyboard_name"] == "Dell USB Keyboard"
    assert seat1["keyboard_id"] == 15

    dlg.destroy()
