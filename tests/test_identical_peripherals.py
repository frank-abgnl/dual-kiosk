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
