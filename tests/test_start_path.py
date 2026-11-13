import subprocess
import sys
import threading
from unittest.mock import MagicMock, patch

from core.autostart import run_startup_reseat_and_launch
from core.session_manager import session_mgr, StartResult


def test_start_session_need_displays_headless():
    """Verify start_session returns NEED_DISPLAYS and does not import GTK (gi)."""
    script = (
        "import sys\n"
        "from unittest.mock import MagicMock\n"
        "from core.session_manager import session_mgr, StartResult\n"
        "session_mgr.dm.probe_and_activate_displays = MagicMock("
        "    return_value=([{'name': 'HDMI-1'}], {'width': 1920, 'height': 1080})\n"
        ")\n"
        "res = session_mgr.start_session()\n"
        "assert res.code == StartResult.NEED_DISPLAYS, f'Unexpected code: {res.code}'\n"
        "assert not res.ok\n"
        "assert 'gi' not in sys.modules, f'gi imported: {sys.modules.get(\"gi\")}'\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"


def test_autostart_retries_with_exponential_backoff():
    """Verify autostart retries with increasing sleeps when start_session fails with retryable codes."""
    sleeps = []

    def mock_sleep(d):
        sleeps.append(d)

    r1 = StartResult(False, StartResult.NEED_DISPLAYS, "Need 2 displays")
    r2 = StartResult(False, StartResult.KIOSK_FAILED, "Firefox failed")
    r3 = StartResult(True, StartResult.OK, "Running")

    stop_evt = threading.Event()

    with patch.object(session_mgr, "start_session", side_effect=[r1, r2, r3]), \
         patch.object(session_mgr, "stop_session") as mock_stop:
        
        # Set stop event immediately after r3 to exit monitoring loop
        def trigger_stop():
            stop_evt.set()

        with patch("core.supervisor.check_spawn_guard"), \
             patch("core.supervisor.spawn_control_board"), \
             patch("core.supervisor.HotkeyManager") as mock_hk:
            instance = mock_hk.return_value
            instance.start.side_effect = trigger_stop
            
            rc = run_startup_reseat_and_launch(stop_event=stop_evt, sleep_fn=mock_sleep)
            assert rc == 0
            assert sleeps == [2, 4]
            mock_stop.assert_called_once()
