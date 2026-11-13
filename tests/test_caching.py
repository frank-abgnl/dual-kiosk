import pytest
from unittest.mock import patch, MagicMock
from core.device_manager import DeviceManager
from core.autostart import is_autostart_enabled


def test_get_displays_cached_within_ttl():
    dm = DeviceManager()
    DeviceManager.clear_cache()

    dummy_xrandr = (
        "Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384\n"
        "HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 531mm x 299mm\n"
        "   1920x1080     60.00*+\n"
    )

    with patch("core.device_manager.run_command", return_value=(dummy_xrandr, "", 0)) as mock_run:
        # First call executes xrandr
        displays1, total1 = dm.get_displays(force=False)
        assert mock_run.call_count == 1
        assert len(displays1) == 1
        assert displays1[0]["name"] == "HDMI-1"

        # Second call within TTL returns cached result WITHOUT re-running xrandr
        displays2, total2 = dm.get_displays(force=False)
        assert mock_run.call_count == 1
        assert displays2 == displays1

        # Third call with force=True re-executes xrandr
        displays3, total3 = dm.get_displays(force=True)
        assert mock_run.call_count == 2


def test_get_xinput_hierarchy_cached_within_ttl():
    dm = DeviceManager()
    DeviceManager.clear_cache()

    dummy_xinput = (
        "⎡ Virtual core pointer                    id=2\t[master pointer  (3)]\n"
        "⎜   ↳ Virtual core XTEST pointer              id=4\t[slave  pointer  (2)]\n"
        "⎜   ↳ USB Optical Mouse                       id=10\t[slave  pointer  (2)]\n"
        "⎣ Virtual core keyboard                   id=3\t[master keyboard (2)]\n"
        "    ↳ Virtual core XTEST keyboard             id=5\t[slave  keyboard (3)]\n"
        "    ↳ USB Keyboard                            id=11\t[slave  keyboard (3)]\n"
    )

    with patch("core.device_manager.run_command", return_value=(dummy_xinput, "", 0)) as mock_run:
        # First call executes xinput list
        m1, p1, k1 = dm.get_xinput_hierarchy(force=False)
        assert mock_run.call_count == 1

        # Second call returns cached result
        m2, p2, k2 = dm.get_xinput_hierarchy(force=False)
        assert mock_run.call_count == 1
        assert len(p2) == len(p1)

        # Force call bypasses cache
        m3, p3, k3 = dm.get_xinput_hierarchy(force=True)
        assert mock_run.call_count == 2


def test_is_autostart_enabled_cached():
    with patch("core.autostart.run_command", return_value=("enabled\n", "", 0)) as mock_run:
        with patch("core.autostart.SYSTEMD_SERVICE_PATH") as mock_path:
            mock_path.exists.return_value = False
            # First call executes systemctl
            res1 = is_autostart_enabled(force=True)
            assert res1 is True
            initial_count = mock_run.call_count

            # Second call within TTL uses cache
            res2 = is_autostart_enabled(force=False)
            assert res2 is True
            assert mock_run.call_count == initial_count
