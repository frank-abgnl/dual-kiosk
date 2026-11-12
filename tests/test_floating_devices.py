from pathlib import Path
from unittest.mock import MagicMock, patch

from core.device_manager import DeviceManager
from core.xinput_seating import XInputSeatingEngine


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "xinput_list_with_floating.txt"


def test_parse_floating_slaves():
    """Verify fixture xinput_list_with_floating.txt parses floating devices into correct pools."""
    fixture_content = FIXTURE_PATH.read_text(encoding="utf-8")
    dm = DeviceManager()

    def mock_run_cmd(cmd, **kwargs):
        if cmd == ["xinput", "list"]:
            return fixture_content, "", 0
        if cmd[:2] == ["xinput", "list"]:
            dev_id = cmd[2]
            if dev_id == "8":
                return "Buttons supported: 7\nButton labels: ...", "", 0
            if dev_id == "9":
                return "Reporting 248 keys\nKey labels: ...", "", 0
        return "", "", 0

    with patch("core.device_manager.run_command", side_effect=mock_run_cmd):
        masters, pointers, keyboards = dm.get_xinput_hierarchy()

    # Verify floating pointer
    f_ptr = next((p for p in pointers if p["id"] == 8), None)
    assert f_ptr is not None, "Floating mouse id 8 not found in pointers"
    assert f_ptr["floating"] is True
    assert f_ptr["attachment"] is None

    # Verify floating keyboard
    f_kbd = next((k for k in keyboards if k["id"] == 9), None)
    assert f_kbd is not None, "Floating keyboard id 9 not found in keyboards"
    assert f_kbd["floating"] is True
    assert f_kbd["attachment"] is None

    # Verify attached slave has floating=False
    att_ptr = next(p for p in pointers if p["id"] == 6)
    assert att_ptr["floating"] is False
    assert att_ptr["attachment"] == 2


def test_sleep_and_wake_seat_calls_reattach():
    """Verify sleep_seat followed by wake_seat reattaches floated devices."""
    fixture_content = FIXTURE_PATH.read_text(encoding="utf-8")
    engine = XInputSeatingEngine()
    commands = []

    def mock_run_cmd(cmd, **kwargs):
        commands.append(cmd)
        if cmd == ["xinput", "list"]:
            return fixture_content, "", 0
        if cmd[:2] == ["xinput", "list"]:
            dev_id = cmd[2]
            if dev_id == "8":
                return "Buttons supported: 7", "", 0
            if dev_id == "9":
                return "Reporting 248 keys", "", 0
        return "", "", 0

    seat_cfg = {
        "id": "seat2",
        "display": "DP-1",
        "mouse_id": 8,
        "mouse_name": "Floating USB Optical Mouse",
        "keyboard_id": 9,
        "keyboard_name": "Floating USB Keyboard",
        "is_primary": False,
    }
    displays = [{"name": "DP-1", "width": 1920, "height": 1080, "x": 1920, "y": 0, "active": True}]

    with patch("core.device_manager.run_command", side_effect=mock_run_cmd), \
         patch("core.xinput_seating.run_command", side_effect=mock_run_cmd), \
         patch.object(engine, "create_master_pair", return_value=(10, 11)):
        
        # 1. sleep_seat floats devices
        engine.seat_pointers["seat2"] = 10
        engine.seat_keyboards["seat2"] = 11
        engine.sleep_seat("seat2", seat_cfg)
        assert ["xinput", "float", "8"] in commands
        assert ["xinput", "float", "9"] in commands

        # 2. wake_seat reattaches devices
        engine.wake_seat(seat_cfg, displays, (3840, 1080))

        # Assert xinput reattach is called for both mouse (8) and keyboard (9)
        reattach_8 = any(c[:3] == ["xinput", "reattach", "8"] for c in commands)
        reattach_9 = any(c[:3] == ["xinput", "reattach", "9"] for c in commands)
        assert reattach_8, "Expected xinput reattach for device 8"
        assert reattach_9, "Expected xinput reattach for device 9"
