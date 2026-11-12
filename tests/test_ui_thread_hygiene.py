import time
from unittest.mock import MagicMock
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

Gtk.init_check()

from gui.tabs.devices_tab import DevicesTab
from gui.tabs.seats_tab import SeatsTab
from gui.tabs.config_tab import ConfigTab


def _pump_glib_events(max_wait=1.0):
    """Pumps pending GLib idle/timeout events until queue is empty or max_wait."""
    ctx = GLib.MainContext.default()
    start = time.time()
    while time.time() - start < max_wait:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.02)


def test_devices_tab_bg_action_disables_button():
    mock_app = MagicMock()
    mock_app.device_mgr.get_displays.return_value = ([], (0, 0))
    mock_app.device_mgr.get_xinput_hierarchy.return_value = ({}, [], [])
    mock_app.device_mgr.get_physical_input_devices.return_value = []

    tab = DevicesTab(mock_app)
    btn = Gtk.Button(label="Test")
    assert btn.get_sensitive() is True

    action_called = False

    def action():
        nonlocal action_called
        action_called = True
        return 42

    done_called = False

    def on_done(res, err):
        nonlocal done_called
        done_called = True
        assert res == 42
        assert err is None

    tab._run_bg_action(btn, action, on_done)
    # Immediately after dispatch, button is disabled
    assert btn.get_sensitive() is False

    _pump_glib_events(max_wait=1.0)

    assert action_called is True
    assert done_called is True
    assert btn.get_sensitive() is True


def test_seats_tab_auto_detect_disables_button():
    mock_app = MagicMock()
    mock_app.device_mgr.get_displays.return_value = ([], (0, 0))
    mock_app.device_mgr.get_usable_pointers.return_value = []
    mock_app.device_mgr.get_usable_keyboards.return_value = []
    mock_app.config_mgr.load_config.return_value = {"seats": []}

    tab = SeatsTab(mock_app)
    assert tab.btn_auto.get_sensitive() is True

    tab._on_auto_detect(tab.btn_auto)
    assert tab.btn_auto.get_sensitive() is False

    _pump_glib_events(max_wait=1.0)

    assert mock_app.config_mgr.auto_generate_config.called
    assert tab.btn_auto.get_sensitive() is True


def test_seats_tab_save_config_disables_button():
    mock_app = MagicMock()
    mock_app.device_mgr.get_displays.return_value = ([], (0, 0))
    mock_app.device_mgr.get_usable_pointers.return_value = []
    mock_app.device_mgr.get_usable_keyboards.return_value = []
    mock_app.config_mgr.load_config.return_value = {"seats": []}

    tab = SeatsTab(mock_app)
    tab.card_seat1.get_configured_data = MagicMock(return_value={"id": "seat1"})
    tab.card_seat2.get_configured_data = MagicMock(return_value={"id": "seat2"})

    assert tab.btn_save.get_sensitive() is True
    tab._on_save_config(tab.btn_save)
    assert tab.btn_save.get_sensitive() is False

    _pump_glib_events(max_wait=1.0)

    assert mock_app.config_mgr.update.called
    assert tab.btn_save.get_sensitive() is True


def test_config_tab_save_and_validate_disables_buttons():
    mock_app = MagicMock()
    mock_app.config_mgr.load_config.return_value = {"version": "1.0", "seats": [], "settings": {}}
    mock_app.config_mgr.save_config.return_value = True

    tab = ConfigTab(mock_app)
    _pump_glib_events(max_wait=0.5)

    # Save
    assert tab.btn_save.get_sensitive() is True
    tab._on_save(tab.btn_save)
    assert tab.btn_save.get_sensitive() is False
    _pump_glib_events(max_wait=1.0)
    assert tab.btn_save.get_sensitive() is True
    assert mock_app.config_mgr.save_config.called

    # Validate
    assert tab.btn_validate.get_sensitive() is True
    tab._on_validate(tab.btn_validate)
    assert tab.btn_validate.get_sensitive() is False
    _pump_glib_events(max_wait=1.0)
    assert tab.btn_validate.get_sensitive() is True
