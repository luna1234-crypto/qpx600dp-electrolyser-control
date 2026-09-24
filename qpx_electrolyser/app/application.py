"""Compose the GUI; Milestone 6 keeps physical transport outside the GUI."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
from types import TracebackType

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from qpx_electrolyser import __version__
from qpx_electrolyser.app.connection_controller import ConnectionController
from qpx_electrolyser.app.logging_config import configure_logging
from qpx_electrolyser.app.paths import default_paths
from qpx_electrolyser.gui.main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    """Run a normal window, or briefly exercise its event loop for validation."""
    parser = argparse.ArgumentParser(description="QPX600DP milestone 6 shared-worker build")
    parser.add_argument("--log-dir", type=Path, help="Override the project's logs directory")
    parser.add_argument(
        "--smoke-test", action="store_true", help="Show the window, process events, then close"
    )
    parser.add_argument(
        "--screenshot", type=Path, help="Save a window PNG during --smoke-test only"
    )
    args = parser.parse_args(argv)
    if args.screenshot is not None and not args.smoke_test:
        parser.error("--screenshot requires --smoke-test")

    app = QApplication([sys.argv[0]])
    app.setApplicationName("QPX600DP Electrolyser")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("QPX600DP Lab")

    try:
        session = configure_logging(args.log_dir or default_paths().log_directory)
    except (OSError, RuntimeError):
        message = "Cannot open the application log. Choose a writable --log-dir and restart."
        print(message, file=sys.stderr)
        if not args.smoke_test:
            QMessageBox.critical(None, "Unable to start", message)
        return 1

    logger = session.logger
    previous_exception_hook = sys.excepthook
    window: MainWindow | None = None
    connection_controller: ConnectionController | None = None

    def handle_unhandled_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        logger.critical(
            "Unhandled application error", exc_info=(exception_type, exception, traceback)
        )
        if not args.smoke_test:
            QMessageBox.critical(
                window,
                "Application error",
                f"The application must close. Diagnostic details were saved to:\n{session.file_path}",
            )
        app.exit(1)

    sys.excepthook = handle_unhandled_exception
    try:
        logger.info(
            "Application started: version=%s; shared simulation worker available; physical GUI disabled",
            __version__,
        )
        connection_controller = ConnectionController()
        window = MainWindow(
            log_file=session.file_path,
            connection_controller=connection_controller,
        )
        window.show()

        if args.smoke_test:
            def finish_smoke_test() -> None:
                if args.screenshot is not None:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    if not window.grab().save(str(args.screenshot), "PNG"):
                        raise OSError("Could not write the requested window screenshot.")
                logger.info("Smoke test: window visible=%s; Qt event loop processed events", window.isVisible())
                window.close()

            QTimer.singleShot(350, finish_smoke_test)

        exit_code = app.exec()
        logger.info("Application stopped: exit_code=%d", exit_code)
        return exit_code
    except Exception:
        logger.exception("Application startup failed")
        if not args.smoke_test:
            QMessageBox.critical(
                window, "Unable to start", f"See the application log for details:\n{session.file_path}"
            )
        return 1
    finally:
        sys.excepthook = previous_exception_hook
        if window is not None:
            window.close()
        if connection_controller is not None:
            connection_controller.shutdown(timeout_s=2.0)
        session.close()
