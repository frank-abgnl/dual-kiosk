import pytest
from unittest.mock import MagicMock, patch
from core.session_manager import SessionManager, StartResult


def test_soft_reseat_keeps_kiosks_running_and_forces_recheck():
    sm = SessionManager()

    # Mock 2 running kiosk processes
    proc1 = MagicMock()
    proc1.poll.return_value = None
    proc2 = MagicMock()
    proc2.poll.return_value = None
    sm.kiosk.running_processes = {"seat1": proc1, "seat2": proc2}
    sm.kiosk.window_ids = {"seat1": "0x123", "seat2": "0x456"}

    sm.restart_session = MagicMock()
    sm.engine.reset_all_to_core = MagicMock()
    sm.engine.force_recheck = MagicMock()
    sm.engine.check_and_reseat_hotplugged_devices = MagicMock(return_value=["Reseated mouse on seat2"])
    sm.engine.focus_seat_keyboard = MagicMock()
    sm.dm.get_displays = MagicMock(return_value=([{"name": "HDMI-1"}], {"width": 1920, "height": 1080}))

    with patch("core.hotkey_manager.send_overlay_command") as mock_overlay_cmd:
        ok = sm.reseat_session(hard=False)

        assert ok is True
        mock_overlay_cmd.assert_called_with("HIDE")
        # Soft reseat must NEVER restart the session or reset all to core
        assert not sm.restart_session.called
        assert not sm.engine.reset_all_to_core.called

        # Soft reseat MUST force recheck and check hotplugged devices
        sm.engine.force_recheck.assert_called_once()
        sm.engine.check_and_reseat_hotplugged_devices.assert_called_once()
        assert sm.engine.focus_seat_keyboard.call_count == 2


def test_hard_reseat_restarts_session():
    sm = SessionManager()
    proc1 = MagicMock()
    proc1.poll.return_value = None
    sm.kiosk.running_processes = {"seat1": proc1, "seat2": proc1}

    sm.restart_session = MagicMock(return_value=StartResult(ok=True, code="OK", message="Restarted"))

    with patch("core.hotkey_manager.send_overlay_command"):
        ok = sm.reseat_session(hard=True)
        assert ok is True
        sm.restart_session.assert_called_once()
