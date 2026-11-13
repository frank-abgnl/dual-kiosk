import pytest
from unittest.mock import MagicMock
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

Gtk.init_check()
from gui.tabs.seats_tab import SeatsTab
from core.config_manager import ConfigManager


def test_seats_tab_preserves_device_ident_and_resolution(tmp_path):
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(cfg_file)

    initial_config = {
        "version": "1.0",
        "seats": [
            {
                "id": "seat1",
                "name": "Seat 1",
                "is_primary": True,
                "display": "HDMI-1",
                "resolution": "1920x1080",
                "width": 1920,
                "height": 1080,
                "mouse_id": 10,
                "mouse_name": "Optical Mouse",
                "mouse_product_id": "046d:c077",
                "mouse_ident": {"usb_path": "1-1.2", "serial": "12345"},
                "keyboard_id": 11,
                "keyboard_name": "Gaming Keyboard",
                "keyboard_product_id": "046d:c31c",
                "keyboard_ident": {"usb_path": "1-1.3", "serial": "67890"},
                "lock_peripherals": True,
            },
            {
                "id": "seat2",
                "name": "Seat 2",
                "is_primary": False,
                "display": "DP-1",
                "resolution": "2560x1440",
                "width": 2560,
                "height": 1440,
                "mouse_id": 12,
                "mouse_name": "Wireless Mouse",
                "mouse_product_id": "046d:b016",
                "mouse_ident": {"usb_path": "1-1.4", "serial": "55555"},
                "keyboard_id": 13,
                "keyboard_name": "Standard Keyboard",
                "keyboard_product_id": "046d:c31d",
                "keyboard_ident": {"usb_path": "1-1.5", "serial": "99999"},
                "lock_peripherals": True,
            },
        ],
        "settings": {},
    }
    mgr.save_config(initial_config)

    mock_app = MagicMock()
    mock_app.config_mgr = mgr
    mock_app.device_mgr.get_displays.return_value = ([], (0, 0))
    mock_app.device_mgr.get_usable_pointers.return_value = []
    mock_app.device_mgr.get_usable_keyboards.return_value = []

    tab = SeatsTab(mock_app)
    # Mock cached data and card configured output
    tab._cached_displays = [{"name": "HDMI-1"}, {"name": "DP-1"}]
    tab._cached_pointers = []
    tab._cached_keyboards = []

    # Cards return basic configured data without ident or resolution fields
    tab.card_seat1.get_configured_data = MagicMock(return_value={
        "id": "seat1",
        "name": "Seat 1 (Primary)",
        "is_primary": True,
        "display": "HDMI-1",
        "mouse_id": 10,
        "mouse_name": "Optical Mouse",
        "keyboard_id": 11,
        "keyboard_name": "Gaming Keyboard",
        "lock_peripherals": True,
        "locked_display": "HDMI-1",
        "profile_name": "seat1",
        "kiosk_url": "https://duckduckgo.com",
        "lock_kiosk": True,
        "auto_respawn": True,
    })
    tab.card_seat2.get_configured_data = MagicMock(return_value={
        "id": "seat2",
        "name": "Seat 2 (Secondary)",
        "is_primary": False,
        "display": "DP-1",
        "mouse_id": 12,
        "mouse_name": "Wireless Mouse",
        "keyboard_id": 13,
        "keyboard_name": "Standard Keyboard",
        "lock_peripherals": True,
        "locked_display": "DP-1",
        "profile_name": "seat2",
        "kiosk_url": "https://www.google.com",
        "lock_kiosk": True,
        "auto_respawn": True,
    })

    # Trigger save
    tab._on_save_config(None)

    # Check preserved fields in saved config
    saved = mgr.load_config()
    s1 = saved["seats"][0]
    s2 = saved["seats"][1]

    assert s1["resolution"] == "1920x1080"
    assert s1["width"] == 1920
    assert s1["height"] == 1080
    assert s1["mouse_product_id"] == "046d:c077"
    assert s1["mouse_ident"] == {"usb_path": "1-1.2", "serial": "12345"}
    assert s1["keyboard_product_id"] == "046d:c31c"
    assert s1["keyboard_ident"] == {"usb_path": "1-1.3", "serial": "67890"}

    assert s2["resolution"] == "2560x1440"
    assert s2["width"] == 2560
    assert s2["height"] == 1440
    assert s2["mouse_product_id"] == "046d:b016"
    assert s2["mouse_ident"] == {"usb_path": "1-1.4", "serial": "55555"}
    assert s2["keyboard_product_id"] == "046d:c31d"
    assert s2["keyboard_ident"] == {"usb_path": "1-1.5", "serial": "99999"}
