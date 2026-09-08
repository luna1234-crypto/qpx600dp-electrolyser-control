"""Bounded, UTF-8 diagnostic logs. Experiment data will use a separate logger."""

from dataclasses import dataclass
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


@dataclass(slots=True)
class LoggingSession:
    """Own only this application's handlers and close them explicitly."""

    logger: logging.Logger
    file_path: Path
    handlers: tuple[logging.Handler, ...]

    def close(self) -> None:
        for handler in self.handlers:
            self.logger.removeHandler(handler)
            handler.flush()
            handler.close()
        self.handlers = ()


def configure_logging(log_directory: Path) -> LoggingSession:
    """Append diagnostics, rotate at 5 MiB, and retain five older segments.

    Configure once per process. Fail visibly if another active session exists;
    never remove third-party or root logger handlers to force configuration.
    """
    logger = logging.getLogger("cpx_electrolyser")
    if logger.handlers:
        raise RuntimeError("Application logging is already configured.")

    log_directory.mkdir(parents=True, exist_ok=True)
    file_path = log_directory / f"application_{datetime.now():%Y%m%d}.log"
    file_handler = RotatingFileHandler(
        file_path,
        mode="a",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
        errors="backslashreplace",
        delay=False,
    )
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return LoggingSession(logger, file_path, (file_handler, console_handler))

