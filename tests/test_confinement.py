import pytest
from unittest.mock import MagicMock, patch
from core.pointer_barriers import (
    PointerConfiner,
    ConfinementWatchdog,
    is_point_in_rect,
    nearest_point_in_rect,
)


def test_nearest_point_in_rect():
    rect = (100, 200, 1920, 1080)
    # Inside
    assert nearest_point_in_rect(500, 500, rect) == (500, 500)
    assert is_point_in_rect(500, 500, rect) is True

    # Left
    assert nearest_point_in_rect(50, 500, rect) == (100.0, 500.0)
    assert is_point_in_rect(50, 500, rect) is False

    # Right
    assert nearest_point_in_rect(3000, 500, rect) == (100 + 1920 - 1, 500.0)

    # Top
    assert nearest_point_in_rect(500, 50, rect) == (500.0, 200.0)

    # Bottom
    assert nearest_point_in_rect(500, 2000, rect) == (500.0, 200 + 1080 - 1)

    # Top-left corner
    assert nearest_point_in_rect(10, 10, rect) == (100.0, 200.0)


def test_watchdog_warps_escaping_pointer():
    confiner = PointerConfiner()
    watchdog = ConfinementWatchdog(confiner)

    rect = (1920, 0, 1920, 1080)
    confiner._confined_rects[10] = rect

    mock_dpy = MagicMock()
    mock_root = 123
    mock_x11 = MagicMock()
    mock_xi = MagicMock()

    # Pointer escaped to (500, 500) which is on monitor 1 (rect starts at 1920)
    watchdog.query_pointer = MagicMock(return_value=(True, 500.0, 500.0))

    watchdog.check_all(mock_dpy, mock_root, mock_x11, mock_xi)

    # Must warp pointer to nearest point inside rect: (1920.0, 500.0)
    mock_xi.XIWarpPointer.assert_called_once_with(
        mock_dpy, 10, 0, mock_root, 0.0, 0.0, 0, 0, 1920.0, 500.0
    )
    mock_x11.XFlush.assert_called_once_with(mock_dpy)


def test_watchdog_drops_master_on_error():
    confiner = PointerConfiner()
    watchdog = ConfinementWatchdog(confiner)

    confiner._confined_rects[15] = (0, 0, 1920, 1080)
    confiner._barriers[15] = [999]
    confiner.release = MagicMock(wraps=confiner.release)

    mock_dpy = MagicMock()
    mock_root = 123
    mock_x11 = MagicMock()
    mock_xi = MagicMock()

    # Query pointer returns False (simulating BadDevice / master removed)
    watchdog.query_pointer = MagicMock(return_value=(False, 0.0, 0.0))

    watchdog.check_all(mock_dpy, mock_root, mock_x11, mock_xi)

    # Master 15 must be released and dropped
    confiner.release.assert_called_once_with(15)
    assert 15 not in confiner._confined_rects
