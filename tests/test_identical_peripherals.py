import pytest
from core.peripheral_resolver import PeripheralResolver, PeripheralMemory


def test_identical_devices_assigned_to_bound_usb_port(tmp_path):
    mem = PeripheralMemory(tmp_path / "mem.json")
    resolver = PeripheralResolver()

    # Two identical mice, no serial numbers, distinct USB ports
    dev_a = {
        "id": 10,
        "name": "Optical USB Mouse",
        "product": (0x1234, 0x5678),
        "serial": None,
        "port": "1-1.1",
        "usb_path": "/sys/devices/pci0000:00/usb1/1-1/1-1.1",
    }
    dev_b = {
        "id": 11,
        "name": "Optical USB Mouse",
        "product": (0x1234, 0x5678),
        "serial": None,
        "port": "1-1.2",
        "usb_path": "/sys/devices/pci0000:00/usb1/1-1/1-1.2",
    }

    seats = [
        {"id": "seat1", "is_primary": True, "mouse_name": "Optical USB Mouse", "mouse_port": "1-1.1"},
        {"id": "seat2", "is_primary": False, "mouse_name": "Optical USB Mouse", "mouse_port": "1-1.2"},
    ]
    masters = {"seat1": {"pointer": 2, "keyboard": 3}, "seat2": {"pointer": 12, "keyboard": 13}}

    # Initial resolve: dev_a in 1-1.1 -> seat1; dev_b in 1-1.2 -> seat2
    resolved, assignments, events, spares = resolver.resolve(
        seats, [dev_a, dev_b], [], masters, mem
    )

    assert assignments[("seat1", "pointer")]["id"] == 10
    assert assignments[("seat2", "pointer")]["id"] == 11
    assert resolved[0]["mouse_id"] == 10
    assert resolved[1]["mouse_id"] == 11

    # Now cables are swapped physically: dev_a is plugged into 1-1.2, dev_b into 1-1.1
    dev_a_swapped = dict(dev_a, port="1-1.2", usb_path="/sys/devices/pci0000:00/usb1/1-1/1-1.2")
    dev_b_swapped = dict(dev_b, port="1-1.1", usb_path="/sys/devices/pci0000:00/usb1/1-1/1-1.1")

    resolved2, assignments2, _, _ = resolver.resolve(
        seats, [dev_a_swapped, dev_b_swapped], [], masters, mem
    )

    # Since seat1 is bound to 1-1.1, it gets dev_b (now in 1-1.1)
    # Since seat2 is bound to 1-1.2, it gets dev_a (now in 1-1.2)
    assert assignments2[("seat1", "pointer")]["id"] == 11
    assert assignments2[("seat2", "pointer")]["id"] == 10


def test_identical_devices_swapped_when_binding_is_off(tmp_path):
    mem = PeripheralMemory(tmp_path / "mem.json")
    resolver = PeripheralResolver()

    # Binding is OFF (mouse_port=None)
    seats = [
        {"id": "seat1", "is_primary": True, "mouse_name": "Optical USB Mouse", "mouse_port": None},
        {"id": "seat2", "is_primary": False, "mouse_name": "Optical USB Mouse", "mouse_port": None},
    ]
    masters = {"seat1": {"pointer": 2, "keyboard": 3}, "seat2": {"pointer": 12, "keyboard": 13}}

    dev_a = {
        "id": 10,
        "name": "Optical USB Mouse",
        "product": (0x1234, 0x5678),
        "serial": None,
        "port": "1-1.1",
        "usb_path": "/sys/devices/pci0000:00/usb1/1-1/1-1.1",
    }
    dev_b = {
        "id": 11,
        "name": "Optical USB Mouse",
        "product": (0x1234, 0x5678),
        "serial": None,
        "port": "1-1.2",
        "usb_path": "/sys/devices/pci0000:00/usb1/1-1/1-1.2",
    }

    # Step 1: Initial resolve populates short-term memory
    resolved, assignments, _, _ = resolver.resolve(
        seats, [dev_a, dev_b], [], masters, mem
    )
    assert assignments[("seat1", "pointer")]["id"] == 10
    assert assignments[("seat2", "pointer")]["id"] == 11

    # Step 2: Swap ports with binding OFF
    dev_a_swapped = dict(dev_a, port="1-1.2", usb_path="/sys/devices/pci0000:00/usb1/1-1/1-1.2")
    dev_b_swapped = dict(dev_b, port="1-1.1", usb_path="/sys/devices/pci0000:00/usb1/1-1/1-1.1")

    resolved2, assignments2, _, _ = resolver.resolve(
        seats, [dev_a_swapped, dev_b_swapped], [], masters, mem
    )

    # Position memory assigns device now at port 1-1.1 (dev_b) to seat1,
    # and device now at port 1-1.2 (dev_a) to seat2
    assert assignments2[("seat1", "pointer")]["id"] == 11
    assert assignments2[("seat2", "pointer")]["id"] == 10


def test_resolver_locked_wins(tmp_path):
    mem = PeripheralMemory(tmp_path / "mem.json")
    resolver = PeripheralResolver()

    # Seat 1 locked to Dell Mouse with serial SN_DELL
    seats = [
        {
            "id": "seat1",
            "is_primary": True,
            "mouse_name": "Dell Mouse",
            "mouse_ident": {"name": "Dell Mouse", "serial": "SN_DELL", "product": (1, 2)},
        }
    ]
    masters = {"seat1": {"pointer": 2, "keyboard": 3}}

    dev_dell = {"id": 10, "name": "Dell Mouse", "serial": "SN_DELL", "product": (1, 2)}
    dev_hp = {"id": 11, "name": "HP Mouse", "serial": "SN_HP", "product": (3, 4)}

    resolved, assignments, events, _ = resolver.resolve(seats, [dev_hp, dev_dell], [], masters, mem)
    # Locked device dev_dell wins its slot even though dev_hp was earlier in the pool
    assert assignments[("seat1", "pointer")]["id"] == 10


def test_resolver_memory_restores_after_replug(tmp_path):
    mem = PeripheralMemory(tmp_path / "mem.json")
    resolver = PeripheralResolver()

    # Seat 2 has no locked ident yet, but remembers HP Mouse from a previous session
    now = 1000.0
    mem.remember("seat2", "pointer", {"name": "HP Mouse", "product": (3, 4), "serial": "SN_HP", "usb_path": "/sys/1"}, now)

    seats = [
        {"id": "seat1", "is_primary": True, "mouse_name": "Core Mouse"},
        {"id": "seat2", "is_primary": False, "mouse_name": "Generic Mouse"},
    ]
    masters = {"seat1": {"pointer": 2, "keyboard": 3}, "seat2": {"pointer": 12, "keyboard": 13}}

    dev_hp = {"id": 15, "name": "HP Mouse", "product": (3, 4), "serial": "SN_HP", "usb_path": "/sys/1"}
    dev_other = {"id": 16, "name": "Other Standin Mouse", "product": (9, 9), "serial": "SN_OTHER"}

    resolved, assignments, _, _ = resolver.resolve(seats, [dev_other, dev_hp], [], masters, mem, now=now + 10)
    # HP Mouse restored to seat2 via memory
    assert assignments[("seat2", "pointer")]["id"] == 15


def test_resolver_standin_promoted_at_threshold(tmp_path):
    mem = PeripheralMemory(tmp_path / "mem.json")
    # Set promote_after to 10 seconds for testing
    resolver = PeripheralResolver(promote_after=10.0)

    seats = [
        {"id": "seat2", "is_primary": False, "mouse_name": "Old Locked Mouse", "mouse_ident": {"name": "Old Locked Mouse"}}
    ]
    masters = {"seat2": {"pointer": 12, "keyboard": 13}}

    standin = {"id": 20, "name": "Standin Mouse", "product": (5, 6), "serial": "SN_STANDIN", "usb_path": "/sys/devices/pci/usb1/1-1.3"}

    t0 = 1000.0
    # First resolve connects stand-in
    resolved1, assignments1, events1, _ = resolver.resolve(seats, [standin], [], masters, mem, now=t0)
    assert assignments1[("seat2", "pointer")]["id"] == 20
    assert any(e["type"] == "substitute" for e in events1)

    # Stand-in remains connected past promote_after threshold (t0 + 15s)
    resolved2, assignments2, events2, _ = resolver.resolve(seats, [standin], [], masters, mem, now=t0 + 15.0)
    assert any(e["type"] == "promote" for e in events2)
    # The promoted device now becomes locked
    assert resolved2[0]["mouse_name"] == "Standin Mouse"


def test_resolver_same_device_never_on_two_seats(tmp_path):
    mem = PeripheralMemory(tmp_path / "mem.json")
    resolver = PeripheralResolver()

    # Only one mouse available in total
    single_mouse = {"id": 10, "name": "Solo Mouse", "product": (1, 2), "serial": "SN1"}
    seats = [
        {"id": "seat1", "is_primary": True, "mouse_name": "Solo Mouse"},
        {"id": "seat2", "is_primary": False, "mouse_name": "Solo Mouse"},
    ]
    masters = {"seat1": {"pointer": 2, "keyboard": 3}, "seat2": {"pointer": 12, "keyboard": 13}}

    resolved, assignments, _, _ = resolver.resolve(seats, [single_mouse], [], masters, mem)

    # Mouse 10 assigned to seat 1
    assert assignments[("seat1", "pointer")]["id"] == 10
    # Seat 2 must get None; same device is never assigned to two seats
    assert assignments[("seat2", "pointer")] is None
    assert resolved[0]["mouse_id"] == 10
    assert resolved[1]["mouse_id"] is None
