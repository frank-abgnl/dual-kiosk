import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import pytest

from core.logging_setup import setup_logging


def test_setup_logging_creates_rotating_handler(tmp_path, monkeypatch):
    log_file = tmp_path / "test_launcher.log"
    monkeypatch.setattr("core.logging_setup._configured", False)

    setup_logging(log_file=log_file)

    root = logging.getLogger()
    matching = [
        h for h in root.handlers
        if isinstance(h, RotatingFileHandler) and h.baseFilename == str(log_file.resolve())
    ]
    assert len(matching) == 1

    handler = matching[0]
    assert handler.maxBytes == 1024 * 1024
    assert handler.backupCount == 3

    # Log a message and check file output
    test_logger = logging.getLogger("test_subsystem")
    test_logger.info("Test logging message for pseudo-seating")
    handler.flush()

    assert log_file.exists()
    content = log_file.read_text()
    assert "Test logging message for pseudo-seating" in content

    root.removeHandler(handler)
    handler.close()


def test_setup_logging_idempotent(tmp_path, monkeypatch):
    log_file = tmp_path / "test_idempotent.log"
    monkeypatch.setattr("core.logging_setup._configured", False)

    setup_logging(log_file=log_file)
    handler_count_1 = len(logging.getLogger().handlers)

    # Second call should be a no-op
    setup_logging(log_file=log_file)
    handler_count_2 = len(logging.getLogger().handlers)

    assert handler_count_1 == handler_count_2

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "baseFilename", None) == str(log_file.resolve()):
            root.removeHandler(h)
            h.close()
