import socket
import pytest
from unittest.mock import MagicMock, patch
from core.xinput_seating import XInputSeatingEngine
from core.supervisor import check_spawn_guard
from core.hotkey_manager import OverlayIPCServer


def test_reset_all_to_core_only_removes_seat_masters():
    engine = XInputSeatingEngine()
    engine.confiner.release_all = MagicMock()
    engine.remove_master_pair = MagicMock()
    engine.reattach_device = MagicMock()
    engine.reset_pointer_matrix = MagicMock()

    # Suppose system has Virtual core, a Seat_ master, and a foreign master (e.g. tablet/Wacom)
    existing_masters = {
        "Virtual core": {"pointer_id": 2, "keyboard_id": 3},
        "Seat_SEAT2": {"pointer_id": 14, "keyboard_id": 15},
        "Wacom Tablet Master": {"pointer_id": 20, "keyboard_id": 21},
        "Barrier Synergy Master": {"pointer_id": 30, "keyboard_id": 31},
    }
    engine.get_existing_masters = MagicMock(return_value=existing_masters)

    with patch("core.device_manager.DeviceManager.get_xinput_hierarchy", return_value=({}, [], [])):
        engine.reset_all_to_core()

        # Must remove ONLY Seat_SEAT2
        engine.remove_master_pair.assert_called_once_with("Seat_SEAT2")
        # Must NEVER remove foreign masters
        removed_calls = [call.args[0] for call in engine.remove_master_pair.call_args_list]
        assert "Wacom Tablet Master" not in removed_calls
        assert "Barrier Synergy Master" not in removed_calls
        assert "Virtual core" not in removed_calls


def test_gui_exit_leaves_kiosks_running_when_supervisor_alive():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    Gtk.init_check()
    from gui.app import XInputMultiseatApp

    with patch("gui.app.get_supervisor_pid", return_value=99999), \
         patch("core.session_manager.session_mgr.stop_session") as mock_stop:
        app = XInputMultiseatApp()
        try:
            # Simulate window destroy
            app._on_window_destroy(None)
            # Must NOT stop kiosks because supervisor is alive
            assert not mock_stop.called
        finally:
            app.destroy()


def test_gui_exit_stops_session_when_standalone():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    Gtk.init_check()
    from gui.app import XInputMultiseatApp

    with patch("gui.app.get_supervisor_pid", return_value=None), \
         patch("core.session_manager.session_mgr.stop_session") as mock_stop:
        app = XInputMultiseatApp()
        try:
            app._on_window_destroy(None)
            assert mock_stop.called
        finally:
            app.destroy()


def test_spawn_guard_cooldown(tmp_path):
    guard_file = tmp_path / "spawn_guard"
    with patch("core.supervisor.SPAWN_GUARD_FILE", guard_file):
        mock_sleep = MagicMock()
        # First spawn
        check_spawn_guard(cooldown=10.0, sleep_fn=mock_sleep)
        assert not mock_sleep.called
        assert guard_file.exists()

        # Immediate respawn: must sleep
        check_spawn_guard(cooldown=10.0, sleep_fn=mock_sleep)
        assert mock_sleep.called
        wait_arg = mock_sleep.call_args[0][0]
        assert 8.0 <= wait_arg <= 10.0


def test_overlay_ipc_server_ping_pong_and_no_steal(tmp_path):
    sock_path = tmp_path / "overlay.sock"
    with patch("core.hotkey_manager.SOCKET_PATH", sock_path):
        server1 = OverlayIPCServer(on_command=lambda cmd: None)
        assert server1.start() is True

        # Send PING, expect PONG
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(sock_path))
            client.sendall(b"PING\n")
            resp = client.recv(64).decode("utf-8").strip()
            assert resp == "PONG"

        # Attempt to start server2 on active socket: must detect active server and return False
        server2 = OverlayIPCServer(on_command=lambda cmd: None)
        assert server2.start() is False
        assert sock_path.exists()

        server1.stop()


def test_second_gui_launch_exits_immediately():
    import main
    # When send_overlay_command succeeds (e.g. active GUI exists), main() returns immediately without starting Gtk
    with patch("core.hotkey_manager.send_overlay_command", return_value=True), \
         patch("sys.argv", ["main.py"]):
        # Should not raise and return None
        res = main.main()
        assert res is None
