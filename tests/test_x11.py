import os
from unittest.mock import MagicMock, patch
import pytest

from core import x11
from core.pointer_barriers import PointerConfiner


def test_handler_installed_exactly_once():
    """Verify XSetErrorHandler is installed only once when load_x11 is called multiple times."""
    mock_x11 = MagicMock()
    mock_xi = MagicMock()
    mock_xfixes = MagicMock()

    # Reset module-level caching state for the test
    with patch.object(x11, "_loaded_libs", None), \
         patch.object(x11, "_error_handler_installed", False), \
         patch("ctypes.CDLL", side_effect=[mock_x11, mock_xi, mock_xfixes]):
        
        libs1 = x11.load_x11()
        assert mock_x11.XSetErrorHandler.call_count == 1

        libs2 = x11.load_x11()
        assert mock_x11.XSetErrorHandler.call_count == 1
        assert libs1 == libs2


def test_set_focus_and_client_pointer_nonexistent_window():
    """Calling set_focus or set_client_pointer on a non-existent window does not terminate the process."""
    if not os.environ.get("DISPLAY"):
        pytest.skip("DISPLAY not set")

    confiner = PointerConfiner()
    # Non-existent XID
    fake_xid = 0x07FFFFFF

    # Ensure this doesn't crash or kill the process
    res_focus = confiner.set_focus(3, fake_xid)
    res_client = confiner.set_client_pointer(fake_xid, 2)

    assert isinstance(res_focus, bool)
    assert isinstance(res_client, bool)
