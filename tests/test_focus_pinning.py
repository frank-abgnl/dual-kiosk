import pytest
from unittest.mock import MagicMock
from core.kiosk_manager import KioskManager


def test_notify_window_only_calls_once_per_window_id_until_invalidated():
    km = KioskManager()
    callback = MagicMock()
    km.on_window_ready = callback

    # 1. First call on seat1 with window '0x1000'
    km._notify_window("seat1", "0x1000")
    callback.assert_called_once_with("seat1", "0x1000")

    # 2. Subsequent watchdog ticks with SAME window id must NOT call on_window_ready again
    callback.reset_mock()
    for _ in range(5):
        km._notify_window("seat1", "0x1000")
    assert not callback.called

    # 3. New window id on seat1 (e.g. browser restart) DOES trigger callback
    km._notify_window("seat1", "0x2000")
    callback.assert_called_once_with("seat1", "0x2000")

    # 4. Invalidation (e.g. after overlay dismiss or XI hierarchy change) allows re-binding same window
    callback.reset_mock()
    km.invalidate_focus_bindings("seat1")
    km._notify_window("seat1", "0x2000")
    callback.assert_called_once_with("seat1", "0x2000")


def test_overlay_blocks_notify_window():
    km = KioskManager()
    callback = MagicMock()
    km.on_window_ready = callback

    # Set overlay active on seat1
    km.set_overlay_seat("seat1")

    km._notify_window("seat1", "0x1000")
    # Must NOT call on_window_ready while overlay owns seat1
    assert not callback.called

    # When overlay dismissed, binding is invalidated and succeeds
    km.set_overlay_seat(None)
    km._notify_window("seat1", "0x1000")
    callback.assert_called_once_with("seat1", "0x1000")
