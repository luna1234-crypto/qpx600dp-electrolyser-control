"""Verify diagnostic file lifecycle, preservation and bounded growth."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from qpx_electrolyser.app.logging_config import configure_logging


def test_unicode_logging_is_flushed_and_appended(tmp_path: Path) -> None:
    log_dir = tmp_path / "Imperial College London" / "实验日志"
    session = configure_logging(log_dir)
    try:
        session.logger.info("Sample 欧阳 - first session")
        assert "Sample 欧阳" in session.file_path.read_text(encoding="utf-8")
        path = session.file_path
    finally:
        session.close()
    session = configure_logging(log_dir)
    try:
        session.logger.info("second session")
        contents = path.read_text(encoding="utf-8")
        assert contents.count("first session") == 1
        assert contents.count("second session") == 1
    finally:
        session.close()


def test_rotation_bounds_diagnostic_growth(tmp_path: Path) -> None:
    session = configure_logging(tmp_path)
    try:
        handler = next(h for h in session.handlers if isinstance(h, RotatingFileHandler))
        handler.maxBytes = 240  # Exercise production rotation without writing 30 MiB.
        for index in range(35):
            session.logger.info("rotation record %d: %s", index, "x" * 100)
        files = list(tmp_path.glob("application_*.log*"))
        assert len(files) == 6
        assert "rotation record 34" in session.file_path.read_text(encoding="utf-8")
    finally:
        session.close()


def test_duplicate_configuration_is_rejected_without_touching_root(tmp_path: Path) -> None:
    root_handlers = tuple(logging.getLogger().handlers)
    session = configure_logging(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="already configured"):
            configure_logging(tmp_path)
        assert tuple(logging.getLogger().handlers) == root_handlers
        assert len(session.logger.handlers) == 2
    finally:
        session.close()
        session.close()  # Closing twice is harmless.
    assert not session.logger.handlers


def test_file_in_place_of_directory_fails_cleanly(tmp_path: Path) -> None:
    blocked = tmp_path / "logs"
    blocked.write_text("preserve this file", encoding="utf-8")
    with pytest.raises(OSError):
        configure_logging(blocked)
    assert blocked.read_text(encoding="utf-8") == "preserve this file"
    assert not logging.getLogger("qpx_electrolyser").handlers
