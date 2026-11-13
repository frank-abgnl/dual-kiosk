import os
import subprocess
from pathlib import Path
import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent


def test_uninstall_script_set_u_safe():
    script = PROJECT_DIR / "uninstall.sh"
    # Run with --dry-run so no actual uninstall occurs
    res = subprocess.run(
        ["bash", str(script), "--dry-run"],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
    )
    # Must not exit with unbound variable error (exit code 1 with "unbound variable")
    assert "unbound variable" not in res.stderr
    assert res.returncode == 0


def test_start_script_env_expansion():
    # Verify that when PYTHONPATH is empty, ${SCRIPT_DIR}${PYTHONPATH:+:$PYTHONPATH} has no colon
    script = PROJECT_DIR / "start.sh"
    content = script.read_text()
    assert '${PYTHONPATH:+:$PYTHONPATH}' in content
    assert '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}' in content


def test_launch_script_env_expansion():
    script = PROJECT_DIR / "launch.sh"
    content = script.read_text()
    assert '${PYTHONPATH:+:$PYTHONPATH}' in content
    assert '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}' in content
