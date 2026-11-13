import pytest
from unittest.mock import MagicMock, patch
from core.pointer_barriers import (
    PointerConfiner,
    ConfinementWatchdog,
    is_point_in_rect,
    nearest_point_in_rect,
    compute_barrier_edges,
    POSITIVE_X,
    NEGATIVE_X,
    POSITIVE_Y,
    NEGATIVE_Y,
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


def test_barrier_edge_list_for_edge_and_inner_monitors():
    # 2-monitor horizontal layout: Monitor 1 (0, 0, 1920, 1080), Monitor 2 (1920, 0, 1920, 1080), Total (3840, 1080)
    total_screen = (3840, 1080)

    # Edge monitor 1 (Left): touching screen boundary on left (x=0), top (y=0), bottom (y+h=1080)
    # Only inner boundary is right edge adjacent to Monitor 2
    edges1 = compute_barrier_edges(0, 0, 1920, 1080, total_screen)
    assert len(edges1) == 1
    assert edges1[0] == (1920, 0, 1920, 1080, NEGATIVE_X)

    # Edge monitor 2 (Right): touching boundary on top, bottom, right (x+w=3840)
    # Only inner boundary is left edge adjacent to Monitor 1
    edges2 = compute_barrier_edges(1920, 0, 1920, 1080, total_screen)
    assert len(edges2) == 1
    assert edges2[0] == (1920, 0, 1920, 1080, POSITIVE_X)

    # 3-monitor layout: Monitor 1 (0, 0, 1920, 1080), Monitor 2 (1920, 0, 1920, 1080), Monitor 3 (3840, 0, 1920, 1080), Total (5760, 1080)
    # Inner monitor 2: bounded by Monitor 1 on left and Monitor 3 on right
    edges_inner = compute_barrier_edges(1920, 0, 1920, 1080, (5760, 1080))
    assert len(edges_inner) == 2
    assert (1920, 0, 1920, 1080, POSITIVE_X) in edges_inner
    assert (3840, 0, 3840, 1080, NEGATIVE_X) in edges_inner
