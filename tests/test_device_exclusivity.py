import pytest
from unittest.mock import patch, MagicMock
from core.device_manager import DeviceManager
from core.xinput_seating import XInputSeatingEngine
from core.session_manager import SessionManager, StartResult


def test_auto_detect_single_peripherals():
    dm = DeviceManager()
    with patch.object(dm, "get_displays", return_value=([
        {"name": "HDMI-1", "width": 1920, "height": 1080, "x": 0, "y": 0},
        {"name": "DP-1", "width": 1920, "height": 1080, "x": 1920, "y": 0},
    ], (3840, 1080))):
        with patch.object(dm, "get_usb_pointers", return_value=[{"id": 10, "name": "USB Mouse"}]):
            with patch.object(dm, "get_usb_keyboards", return_value=[{"id": 11, "name": "USB Keyboard"}]):
                with patch.object(dm, "get_usable_pointers", return_value=[{"id": 10, "name": "USB Mouse"}]):
                    with patch.object(dm, "get_usable_keyboards", return_value=[{"id": 11, "name": "USB Keyboard"}]):
                        seats = dm.auto_detect_seat_assignments()
                        assert seats["seat1"]["mouse_id"] == 10
                        assert seats["seat1"]["keyboard_id"] == 11
                        # Seat 2 must not reuse seat 1's devices
                        assert seats["seat2"]["mouse_id"] is None
                        assert seats["seat2"]["mouse_name"] == ""
                        assert seats["seat2"]["keyboard_id"] is None
                        assert seats["seat2"]["keyboard_name"] == ""


def test_apply_seating_never_reattaches_same_device_twice():
    engine = XInputSeatingEngine()
    # Mock create_master_pair to return valid master ids
    engine.create_master_pair = MagicMock(return_value=(20, 21))
    engine.confine_master = MagicMock(return_value=True)

    with patch("core.xinput_seating.run_command", return_value=("", "", 0)) as mock_run:
        # Intentionally give both seats the exact same mouse (10) and keyboard (11)
        seats = [
            {"id": "seat1", "is_primary": True, "mouse_id": 10, "keyboard_id": 11, "display": "HDMI-1"},
            {"id": "seat2", "is_primary": False, "mouse_id": 10, "keyboard_id": 11, "display": "DP-1"},
        ]
        # Bypass resolve_live to test apply_seating's own defense against duplicate device IDs
        with patch.object(engine, "resolve_live", return_value=(seats, {})):
            res = engine.apply_seating(seats)

            assert res.get("seat1_mouse") is True
            assert res.get("seat1_keyboard") is True
            assert res.get("seat2_mouse") is False
            assert res.get("seat2_keyboard") is False

            # Collect all 'xinput reattach <dev> ...' calls
            reattached_devices = []
            for call_args in mock_run.call_args_list:
                cmd = call_args[0][0]
                if len(cmd) >= 3 and cmd[0] == "xinput" and cmd[1] == "reattach":
                    reattached_devices.append(int(cmd[2]))

            # Device 10 and 11 must appear at most once in reattach calls
            assert reattached_devices.count(10) == 1
            assert reattached_devices.count(11) == 1


def test_start_session_requires_full_seats_unless_partial_allowed(tmp_path):
    sm = SessionManager()
    sm.dm.probe_and_activate_displays = MagicMock(return_value=([
        {"name": "HDMI-1", "width": 1920, "height": 1080, "x": 0, "y": 0},
        {"name": "DP-1", "width": 1920, "height": 1080, "x": 1920, "y": 0},
    ], {"width": 3840, "height": 1080}))
    sm.is_session_active = MagicMock(return_value=False)
    sm.stop_session = MagicMock()

    # Seat 2 has no mouse or keyboard
    mock_seats = [
        {"id": "seat1", "is_primary": True, "mouse_id": 10, "keyboard_id": 11, "display": "HDMI-1"},
        {"id": "seat2", "is_primary": False, "mouse_id": None, "keyboard_id": None, "display": "DP-1"},
    ]

    sm.cfg_mgr.load_config = MagicMock(return_value={"seats": mock_seats, "settings": {"allow_partial_seats": False}})
    sm.cfg_mgr.auto_generate_config = MagicMock(return_value={"seats": mock_seats, "settings": {"allow_partial_seats": False}})
    sm.dm.assign_displays_to_seats = MagicMock(return_value=(mock_seats, []))
    sm.engine.resolve_live = MagicMock(return_value=(mock_seats, {}))

    # Without allow_partial, fails with NEED_DEVICES
    res = sm.start_session(allow_partial=False)
    assert not res.ok
    assert res.code == StartResult.NEED_DEVICES
    assert "seat2 mouse" in res.message
    assert "seat2 keyboard" in res.message

    # With allow_partial=True, it proceeds past device check
    with patch.object(sm.engine, "apply_seating", return_value={}):
        with patch.object(sm.kiosk, "launch_dual_kiosks", return_value=True):
            with patch.object(sm, "_start_runtime_services"):
                res_partial = sm.start_session(allow_partial=True)
                assert res_partial.ok
                assert res_partial.code == StartResult.OK
