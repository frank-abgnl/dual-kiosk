"""
Central Logging Configuration for XInput Pseudo-Seating Dual Kiosk.
Sets up standard formatting, console stream output, and a RotatingFileHandler
(1 MB x 3 backups) targeting STATE_DIR / "launcher.log".
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from core.paths import LOG_FILE, ensure_state_dir

_configured = False


def setup_logging(
    log_file: Optional[Path] = None,
    level: int = logging.INFO,
    max_bytes: int = 1024 * 1024,  # 1 MB
    backup_count: int = 3,
) -> None:
    """
    Initializes central logging with a RotatingFileHandler and StreamHandler.
    Idempotent: will not duplicate handlers if called multiple times.
    """
    global _configured
    if _configured:
        return

    ensure_state_dir()
    target_log = log_file or LOG_FILE
    target_log.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    has_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root_logger.handlers
    )
    existing_paths = {
        getattr(h, "baseFilename", None)
        for h in root_logger.handlers
        if isinstance(h, RotatingFileHandler)
    }

    if not has_stream:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        root_logger.addHandler(console_handler)

    if str(target_log.resolve()) not in existing_paths:
        try:
            file_handler = RotatingFileHandler(
                filename=str(target_log),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            root_logger.addHandler(file_handler)
        except Exception as exc:
            logging.warning(f"Could not initialize RotatingFileHandler on {target_log}: {exc}")

    _configured = True
