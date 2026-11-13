import pytest
from core.hotkey_manager import resolve_seat_for_device, HotkeyManager


def test_resolve_seat_with_static_and_dynamic_provider():
    # 1. Static dict
    static_map = {"seat1": 3, "seat2": 15}
    assert resolve_seat_for_device(master_id=3, slave_id=10, seat_keyboards=static_map) == "seat1"
    assert resolve_seat_for_device(master_id=15, slave_id=11, seat_keyboards=static_map) == "seat2"

    # 2. Dynamic callable provider
    dynamic_state = {"seat1": 3, "seat2": 15}
    provider = lambda: dynamic_state

    # Initially master 15 is seat2
    assert resolve_seat_for_device(master_id=15, slave_id=11, seat_keyboards=provider) == "seat2"

    # Recreate masters dynamically (e.g. after reseating/hotplug)
    dynamic_state = {"seat1": 3, "seat2": 25}
    # Master 15 no longer matches; new master 25 matches seat2 immediately without recreating listener
    assert resolve_seat_for_device(master_id=25, slave_id=11, seat_keyboards=provider) == "seat2"
    # Old master 15 is not core keyboard (3), so falls back to seat2; core keyboard 3 resolves to seat1
    assert resolve_seat_for_device(master_id=3, slave_id=999, seat_keyboards=provider) == "seat1"


def test_hotkey_manager_dynamic_provider():
    dynamic_state = {"seat1": 3, "seat2": 15}
    triggered = []

    hm = HotkeyManager(
        on_trigger=lambda seat, master, slave: triggered.append((seat, master, slave)),
        seat_keyboards=lambda: dynamic_state,
    )

    assert hm.get_seat_keyboards is not None

    # Simulate trigger on seat2's initial master
    hm._handle_shortcut_trigger(master_id=15, slave_id=10)
    assert triggered[-1] == ("seat2", 15, 10)

    # Update dynamic state without touching hm
    dynamic_state["seat2"] = 35
    hm._handle_shortcut_trigger(master_id=35, slave_id=10)
    assert triggered[-1] == ("seat2", 35, 10)
