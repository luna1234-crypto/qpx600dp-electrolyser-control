"""Compose the Milestone 8 guarded manual-control and CSV-logging GUI."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
import tempfile
import time
from types import TracebackType

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from qpx_electrolyser import __version__
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.app.logging_config import configure_logging
from qpx_electrolyser.app.paths import default_paths
from qpx_electrolyser.gui.main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    """Run a normal window, or briefly exercise its event loop for validation."""
    parser = argparse.ArgumentParser(description="QPX600DP milestone 8 manual controls and CSV logging")
    parser.add_argument("--log-dir", type=Path, help="Override the project's logs directory")
    parser.add_argument(
        "--smoke-test", action="store_true", help="Show the window, process events, then close"
    )
    parser.add_argument(
        "--screenshot", type=Path, help="Save a window PNG during --smoke-test only"
    )
    parser.add_argument("--data-dir", type=Path, help="Initial user-selectable CSV output directory")
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
    smoke_data_directory: tempfile.TemporaryDirectory[str] | None = None

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
            "Application started: version=%s; Simulation Mode default; guarded explicit-port mode available",
            __version__,
        )
        connection_controller = ConnectionController()
        if args.smoke_test and args.data_dir is None:
            smoke_data_directory = tempfile.TemporaryDirectory(prefix="qpx600dp-smoke-")
        data_directory = (
            args.data_dir
            or (Path(smoke_data_directory.name) if smoke_data_directory is not None else None)
        )
        window = MainWindow(
            log_file=session.file_path,
            connection_controller=connection_controller,
            data_directory=data_directory,
        )
        window.show()

        if args.smoke_test:
            smoke_deadline = time.monotonic() + 5

            def fail_smoke_test(message: str) -> None:
                raise RuntimeError(message)

            def finish_smoke_test() -> None:
                if connection_controller.snapshot.state is not ConnectionState.DISCONNECTED:
                    if time.monotonic() >= smoke_deadline:
                        fail_smoke_test("Simulation Mode smoke test did not disconnect in time.")
                    QTimer.singleShot(25, finish_smoke_test)
                    return
                logger.info("Smoke test: simulator disconnected cleanly")
                window.close()

            def verify_connected_smoke_test() -> None:
                state = connection_controller.snapshot.state
                if state is ConnectionState.ERROR:
                    fail_smoke_test("Simulation Mode smoke-test connection failed.")
                if state is not ConnectionState.CONNECTED:
                    if time.monotonic() >= smoke_deadline:
                        fail_smoke_test("Simulation Mode smoke test did not connect in time.")
                    QTimer.singleShot(25, verify_connected_smoke_test)
                    return
                if len(connection_controller.snapshot.initial_readbacks) != 2:
                    fail_smoke_test("Simulation Mode smoke test did not read both channels.")
                for logger_controller in window.logging_controllers.values():
                    if not logger_controller.start(window.data_directory, 0.1):
                        fail_smoke_test(logger_controller.snapshot.message)
                QTimer.singleShot(25, verify_logging_smoke_test)

            def verify_logging_smoke_test() -> None:
                snapshots = [item.snapshot for item in window.logging_controllers.values()]
                if any(item.error for item in snapshots):
                    fail_smoke_test("Simulation Mode smoke-test CSV logging failed.")
                if any(item.sample_count < 1 for item in snapshots):
                    if time.monotonic() >= smoke_deadline:
                        fail_smoke_test("Simulation Mode smoke test did not log both channels.")
                    QTimer.singleShot(25, verify_logging_smoke_test)
                    return
                for logger_controller in window.logging_controllers.values():
                    logger_controller.stop("Smoke-test logging complete")
                if args.screenshot is not None:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    if not window.grab().save(str(args.screenshot), "PNG"):
                        raise OSError("Could not write the requested window screenshot.")
                logger.info(
                    "Smoke test: window visible=%s; Qt event loop processed events; "
                    "simulator connected, both channels read and independent CSV logs written",
                    window.isVisible(),
                )
                connection_controller.request_disconnect()
                QTimer.singleShot(25, finish_smoke_test)

            connection_controller.request_connect()
            QTimer.singleShot(0, verify_connected_smoke_test)

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
        if smoke_data_directory is not None:
            smoke_data_directory.cleanup()
        session.close()
