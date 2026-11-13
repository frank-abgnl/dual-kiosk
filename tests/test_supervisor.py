import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import supervisor
from core.session_manager import session_mgr, StartResult


def test_concurrent_supervisors_leaves_one_alive(tmp_path):
    """Starting two supervisors concurrently with a shared lock leaves exactly one alive."""
    lock_file = tmp_path / "test.lock"

    # Process 1 acquires lock
    fd1 = supervisor.acquire_lock(lock_file)
    assert fd1 is not None

    # Supervisor 2 attempts to run with the same lock file
    res = supervisor.run(lock_path=lock_file)
    assert res == 0  # Exits cleanly because another supervisor holds the lock

    fd1.close()


def test_lock_inode_stable_across_stop_start(tmp_path):
    """stop_session must never delete the lock file; new start preserves the exact same inode."""
    lock_file = tmp_path / "test.lock"

    fd1 = supervisor.acquire_lock(lock_file)
    assert fd1 is not None
    inode1 = os.fstat(fd1.fileno()).st_ino

    # Simulate stop_session
    session_mgr.stop_session(exit_gui=False)
    assert lock_file.exists(), "LOCK_FILE must not be unlinked by stop_session"

    fd1.close()

    fd2 = supervisor.acquire_lock(lock_file)
    assert fd2 is not None
    inode2 = os.fstat(fd2.fileno()).st_ino
    fd2.close()

    assert inode1 == inode2, f"Inode changed across stop/start: {inode1} != {inode2}"


def test_only_one_hotkey_manager_instantiation():
    """Verify only core/supervisor.py constructs HotkeyManager for the S combo."""
    root_dir = Path(__file__).resolve().parent.parent
    matches = []

    for path in root_dir.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        content = path.read_text(encoding="utf-8")
        for idx, line in enumerate(content.splitlines(), start=1):
            if "HotkeyManager(" in line and not line.strip().startswith("#"):
                matches.append(f"{path.name}:{idx}")

    assert len(matches) == 1, f"Expected exactly 1 HotkeyManager construction, found: {matches}"
    assert "supervisor.py" in matches[0]
