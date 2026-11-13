import pytest
from unittest.mock import patch
from core.session_manager import session_mgr


def test_app_shares_core_singletons():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    Gtk.init_check()
    from gui.app import XInputMultiseatApp

    with patch.object(session_mgr.engine, "discover_masters") as mock_discover:
        with patch("gui.app.get_supervisor_pid", return_value=12345):
            app = XInputMultiseatApp()
            try:
                assert app.seating_engine is session_mgr.engine
                assert app.device_mgr is session_mgr.dm
                assert app.kiosk_mgr is session_mgr.kiosk
                assert app.config_mgr is session_mgr.cfg_mgr
                assert app.kiosk_mgr.on_window_ready == session_mgr.engine.focus_seat_keyboard
                assert mock_discover.called
            finally:
                app.destroy()
