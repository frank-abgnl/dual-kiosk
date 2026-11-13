import pytest
from core.device_manager import DeviceManager
from core.xinput_seating import XInputSeatingEngine


def test_assign_displays_locked_preference():
    dm = DeviceManager()
    seats = [
        {"id": "seat1", "locked_display": "HDMI-1", "display": "DP-1"},
        {"id": "seat2", "locked_display": "DP-1", "display": "HDMI-1"},
    ]
    displays = [
        {"name": "DP-1", "connected": True, "width": 1920, "height": 1080, "x": 0, "y": 0},
        {"name": "HDMI-1", "connected": True, "width": 1920, "height": 1080, "x": 1920, "y": 0},
    ]

    updated, msgs = dm.assign_displays_to_seats(seats, displays)
    # Locked preference wins
    assert updated[0]["display"] == "HDMI-1"
    assert updated[1]["display"] == "DP-1"


def test_assign_displays_new_display_fills_gap():
    dm = DeviceManager()
    # Seat 2 was on DP-1 which is now disconnected; newly plugged display is VGA-1
    seats = [
        {"id": "seat1", "locked_display": "HDMI-1", "display": "HDMI-1"},
        {"id": "seat2", "locked_display": "DP-1", "display": "DP-1"},
    ]
    displays = [
        {"name": "HDMI-1", "connected": True, "width": 1920, "height": 1080, "x": 0, "y": 0},
        {"name": "VGA-1", "connected": True, "width": 1920, "height": 1080, "x": 1920, "y": 0},
    ]

    updated, msgs = dm.assign_displays_to_seats(seats, displays)
    assert updated[0]["display"] == "HDMI-1"
    assert updated[1]["display"] == "VGA-1"


def test_assign_displays_no_duplicate_display():
    dm = DeviceManager()
    # Both seats requesting same display HDMI-1, but second display DP-1 is present
    seats = [
        {"id": "seat1", "display": "HDMI-1"},
        {"id": "seat2", "display": "HDMI-1"},
    ]
    displays = [
        {"name": "HDMI-1", "connected": True, "width": 1920, "height": 1080, "x": 0, "y": 0},
        {"name": "DP-1", "connected": True, "width": 1920, "height": 1080, "x": 1920, "y": 0},
    ]

    updated, msgs = dm.assign_displays_to_seats(seats, displays)
    assigned = [s["display"] for s in updated]
    assert len(set(assigned)) == 2
    assert "HDMI-1" in assigned
    assert "DP-1" in assigned


def test_find_conflicts_duplicates():
    # Duplicate mouse
    cfg_dup_mouse = [
        {"id": "seat1", "mouse_id": 5, "keyboard_id": 10, "display": "HDMI-1"},
        {"id": "seat2", "mouse_id": 5, "keyboard_id": 11, "display": "DP-1"},
    ]
    conflicts = XInputSeatingEngine.find_conflicts(cfg_dup_mouse)
    assert any("mouse 5" in c for c in conflicts)

    # Duplicate keyboard
    cfg_dup_kbd = [
        {"id": "seat1", "mouse_id": 5, "keyboard_id": 10, "display": "HDMI-1"},
        {"id": "seat2", "mouse_id": 6, "keyboard_id": 10, "display": "DP-1"},
    ]
    conflicts = XInputSeatingEngine.find_conflicts(cfg_dup_kbd)
    assert any("keyboard 10" in c for c in conflicts)

    # Duplicate display
    cfg_dup_disp = [
        {"id": "seat1", "mouse_id": 5, "keyboard_id": 10, "display": "HDMI-1"},
        {"id": "seat2", "mouse_id": 6, "keyboard_id": 11, "display": "HDMI-1"},
    ]
    conflicts = XInputSeatingEngine.find_conflicts(cfg_dup_disp)
    assert any("display 'HDMI-1'" in c for c in conflicts)

    # No duplicates
    cfg_clean = [
        {"id": "seat1", "mouse_id": 5, "keyboard_id": 10, "display": "HDMI-1"},
        {"id": "seat2", "mouse_id": 6, "keyboard_id": 11, "display": "DP-1"},
    ]
    assert XInputSeatingEngine.find_conflicts(cfg_clean) == []
