import pytest
from core.combo_hotkey import ComboTracker


def test_combo_fires_once():
    tracker = ComboTracker(required=("CTRL", "SHIFT", "M", "KEY"), debounce=0.8)

    t0 = 100.0
    assert tracker.event(1, "CTRL", True, now=t0) is False
    assert tracker.event(1, "SHIFT", True, now=t0) is False
    assert tracker.event(1, "M", True, now=t0) is False
    # Final key pressed completes the combo
    assert tracker.event(1, "KEY", True, now=t0) is True

    # Repeated key event while held returns False
    assert tracker.event(1, "KEY", True, now=t0 + 0.1) is False
    assert tracker.event(1, "M", True, now=t0 + 0.2) is False


def test_combo_debounce():
    tracker = ComboTracker(required=("CTRL", "KEY"), debounce=0.8)

    t0 = 100.0
    tracker.event(1, "CTRL", True, now=t0)
    assert tracker.event(1, "KEY", True, now=t0) is True

    # Release KEY
    tracker.event(1, "KEY", False, now=t0 + 0.1)

    # Press KEY again within debounce (0.3s < 0.8s) -> should NOT fire
    assert tracker.event(1, "KEY", True, now=t0 + 0.3) is False

    # Release again
    tracker.event(1, "KEY", False, now=t0 + 0.4)

    # Press KEY after debounce elapsed (1.0s > 0.8s) -> should fire
    assert tracker.event(1, "KEY", True, now=t0 + 1.0) is True


def test_combo_release_resets():
    tracker = ComboTracker(required=("CTRL", "KEY"), debounce=0.5)

    t0 = 100.0
    tracker.event(1, "CTRL", True, now=t0)
    assert tracker.event(1, "KEY", True, now=t0) is True
    assert tracker._held is True

    # Release a required key
    tracker.event(1, "KEY", False, now=t0 + 0.1)
    assert tracker._held is False

    # Re-press after debounce
    assert tracker.event(1, "KEY", True, now=t0 + 0.7) is True
    assert tracker._held is True


def test_combo_per_device_isolation():
    tracker = ComboTracker(required=("CTRL", "SHIFT", "M", "KEY"), debounce=0.8)

    t0 = 100.0
    # Device 1 presses CTRL and SHIFT
    assert tracker.event(1, "CTRL", True, now=t0) is False
    assert tracker.event(1, "SHIFT", True, now=t0) is False

    # Device 2 presses M and KEY
    assert tracker.event(2, "M", True, now=t0) is False
    assert tracker.event(2, "KEY", True, now=t0) is False

    # Neither device has all keys, so combo has not fired on either
    assert tracker._held is False

    # Now Device 1 presses M and KEY
    assert tracker.event(1, "M", True, now=t0) is False
    assert tracker.event(1, "KEY", True, now=t0) is True
