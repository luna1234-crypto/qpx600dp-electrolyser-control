"""Milestone 8 independent CSV logging with simulator/fake backends only."""

import csv
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import current_thread
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.data.csv_logging import (
    CSV_COLUMNS,
    ChannelLoggingController,
    CsvLogFile,
)
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.channel_readback import ChannelReadback
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate, timeout_s: float = 3) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for Qt state change")


def connected(qapp: QApplication, backend=None) -> ConnectionController:  # type: ignore[no-untyped-def]
    controller = ConnectionController(lambda: backend or SimulatedPowerSupply())
    controller.request_connect()
    wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)
    return controller


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_csv_schema_values_flush_exclusive_creation_and_close(tmp_path: Path) -> None:
    created = datetime(2026, 10, 7, 12, 34, 56, 123456, tzinfo=timezone.utc)
    writer = CsvLogFile(tmp_path, 1, created_at=created)
    expected_path = tmp_path / "qpx600dp_ch1_20261007T123456_123456Z.csv"
    assert writer.path == expected_path
    assert expected_path.read_text(encoding="utf-8").splitlines()[0].split(",") == list(CSV_COLUMNS)
    readback = ChannelReadback(1, 4.0, 0.08, 3.99, 0.04, "80 V", False)
    writer.append(
        timestamp=created,
        elapsed_s=1.25,
        channel=1,
        readback=readback,
        connection_state="connected",
        session_id=3,
        status="ok",
    )
    assert rows(expected_path)[0] == {
        "timestamp_iso": "2026-10-07T12:34:56.123Z",
        "elapsed_seconds": "1.250000",
        "channel": "1",
        "programmed_voltage_v": "4",
        "programmed_current_limit_a": "0.08",
        "measured_voltage_v": "3.99",
        "measured_current_a": "0.04",
        "voltage_range": "80 V",
        "output_state": "OFF",
        "connection_state": "connected",
        "session_id": "3",
        "sample_status": "ok",
        "error": "",
    }
    with pytest.raises(FileExistsError):
        CsvLogFile(tmp_path, 1, created_at=created)
    writer.close()
    writer.close()
    with pytest.raises(ValueError, match="closed"):
        writer.append(
            timestamp=created, elapsed_s=2, channel=1, readback=readback,
            connection_state="connected", session_id=3, status="ok",
        )


@pytest.mark.parametrize("channel", [1, 2])
def test_each_channel_logs_independently(
    qapp: QApplication, tmp_path: Path, channel: int,
) -> None:
    connection = connected(qapp)
    logger = ChannelLoggingController(channel, connection)
    try:
        assert logger.start(tmp_path, 0.1)
        wait_until(qapp, lambda: logger.snapshot.sample_count >= 2)
        logger.stop()
        data = rows(logger.snapshot.file_path)  # type: ignore[arg-type]
        assert data and {item["channel"] for item in data} == {str(channel)}
        assert all(item["sample_status"] == "ok" for item in data)
        assert all(item["connection_state"] == "connected" for item in data)
        assert logger.snapshot.file_path is not None
        with logger.snapshot.file_path.open("a", encoding="utf-8") as stream:
            stream.write("")
    finally:
        logger.shutdown()
        assert connection.shutdown(1)


def test_simultaneous_logging_is_serialized_on_shared_worker(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class ThreadRecordingSimulator(SimulatedPowerSupply):
        def __init__(self) -> None:
            super().__init__(SimulatorConfig(communication_delay_s=0.002))
            self.read_threads: list[str] = []

        def read_set_voltage(self, channel: int) -> float:
            self.read_threads.append(current_thread().name)
            return super().read_set_voltage(channel)

    backend = ThreadRecordingSimulator()
    connection = connected(qapp, backend)
    loggers = [ChannelLoggingController(channel, connection) for channel in (1, 2)]
    try:
        assert all(logger.start(tmp_path, 0.1) for logger in loggers)
        wait_until(qapp, lambda: all(logger.snapshot.sample_count >= 2 for logger in loggers))
        for logger in loggers:
            logger.stop()
        assert {rows(logger.snapshot.file_path)[0]["channel"] for logger in loggers} == {"1", "2"}  # type: ignore[arg-type]
        assert backend.read_threads and set(backend.read_threads) == {"InstrumentCommandWorker"}
    finally:
        for logger in loggers:
            logger.shutdown()
        assert connection.shutdown(1)


def test_delayed_samples_never_overlap_and_missed_slots_are_counted(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0.04))
    connection = connected(qapp, backend)
    logger = ChannelLoggingController(1, connection)
    try:
        assert logger.start(tmp_path, 0.1)
        wait_until(qapp, lambda: logger.snapshot.sample_count >= 2)
        logger.stop()
        data = rows(logger.snapshot.file_path)  # type: ignore[arg-type]
        elapsed = [float(item["elapsed_seconds"]) for item in data]
        assert elapsed == sorted(elapsed)
        assert logger.snapshot.missed_samples >= 1
    finally:
        logger.shutdown()
        assert connection.shutdown(1)


def test_disconnect_stops_and_reconnect_requires_a_new_explicit_start(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = connected(qapp)
    logger = ChannelLoggingController(1, connection)
    try:
        assert logger.start(tmp_path, 0.1)
        wait_until(qapp, lambda: logger.snapshot.sample_count >= 1)
        old_path = logger.snapshot.file_path
        connection.request_disconnect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.DISCONNECTED)
        assert not logger.snapshot.active
        assert rows(old_path)[-1]["sample_status"] == "disconnecting"  # type: ignore[arg-type]
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        qapp.processEvents()
        assert not logger.snapshot.active
        assert logger.start(tmp_path, 0.1)
        assert logger.snapshot.file_path != old_path
    finally:
        logger.shutdown()
        assert connection.shutdown(1)


def test_missing_directory_and_writer_failure_are_channel_local(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = connected(qapp)
    healthy = ChannelLoggingController(2, connection)
    broken = ChannelLoggingController(1, connection, writer_factory=lambda *_args: (_ for _ in ()).throw(PermissionError("denied")))
    try:
        assert not broken.start(tmp_path, 0.1)
        assert "denied" in broken.snapshot.message
        assert not ChannelLoggingController(1, connection).start(tmp_path / "missing", 0.1)
        assert healthy.start(tmp_path, 0.1)
        wait_until(qapp, lambda: healthy.snapshot.sample_count >= 1)
        assert connection.snapshot.state is ConnectionState.CONNECTED
    finally:
        broken.shutdown()
        healthy.shutdown()
        assert connection.shutdown(1)


def test_incremental_write_failure_stops_only_affected_logger(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingWriter:
        def __init__(self, _directory: Path, channel: int) -> None:
            self.path = tmp_path / f"fail-{channel}.csv"
            self.closed = False

        def append(self, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            raise OSError("disk full")

        def close(self) -> None:
            self.closed = True

    connection = connected(qapp)
    bad = ChannelLoggingController(1, connection, writer_factory=FailingWriter)
    good = ChannelLoggingController(2, connection)
    try:
        assert bad.start(tmp_path, 0.1) and good.start(tmp_path, 0.1)
        wait_until(qapp, lambda: not bad.snapshot.active and good.snapshot.sample_count >= 1)
        assert "disk full" in bad.snapshot.message
        assert good.snapshot.active
        assert connection.snapshot.state is ConnectionState.CONNECTED
    finally:
        bad.shutdown()
        good.shutdown()
        assert connection.shutdown(1)


def test_malformed_and_backend_errors_close_their_logger(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qpx_electrolyser.data.csv_logging as module

    connection = connected(qapp)
    malformed = ChannelLoggingController(1, connection)
    monkeypatch.setattr(module, "read_channel", lambda _supply, _channel: "bad")
    try:
        assert malformed.start(tmp_path, 0.1)
        wait_until(qapp, lambda: not malformed.snapshot.active)
        assert "malformed" in malformed.snapshot.message
        assert rows(malformed.snapshot.file_path)[-1]["sample_status"] == "malformed"  # type: ignore[arg-type]
    finally:
        malformed.shutdown()
        assert connection.shutdown(1)


def test_backend_read_failure_is_recorded_and_does_not_retry(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingReadSimulator(SimulatedPowerSupply):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def read_set_voltage(self, channel: int) -> float:
            self.attempts += 1
            if self.attempts > 2:
                raise RuntimeError("injected read failure")
            return super().read_set_voltage(channel)

    backend = FailingReadSimulator()
    connection = connected(qapp, backend)
    logger = ChannelLoggingController(1, connection)
    try:
        assert logger.start(tmp_path, 0.1)
        wait_until(qapp, lambda: not logger.snapshot.active)
        time.sleep(0.15)
        qapp.processEvents()
        assert backend.attempts == 3
        final = rows(logger.snapshot.file_path)[-1]  # type: ignore[arg-type]
        assert final["sample_status"] == "backend-error"
        assert "injected read failure" in final["error"]
        assert connection.snapshot.state is ConnectionState.CONNECTED
    finally:
        logger.shutdown()
        assert connection.shutdown(1)


def test_application_style_shutdown_cancels_timer_and_closes_file(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = connected(qapp)
    logger = ChannelLoggingController(1, connection)
    assert logger.start(tmp_path, 0.1)
    wait_until(qapp, lambda: logger.snapshot.sample_count >= 1)
    logger.shutdown()
    count = logger.snapshot.sample_count
    time.sleep(0.15)
    qapp.processEvents()
    assert not logger.snapshot.active
    assert logger.snapshot.sample_count == count
    assert connection.shutdown(1)


def test_gui_has_independent_start_stop_controls_and_closes_both_on_exit(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController()
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show()
    try:
        assert all(not panel.start_logging_button.isEnabled() for panel in window.channel_panels.values())
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert all(panel.start_logging_button.isEnabled() for panel in window.channel_panels.values())
        window.logging_interval.setValue(0.1)
        window.channel_panels[1].start_logging_button.click()
        assert window.logging_controllers[1].snapshot.active
        assert not window.logging_controllers[2].snapshot.active
        window.channel_panels[2].start_logging_button.click()
        wait_until(qapp, lambda: all(item.snapshot.sample_count >= 1 for item in window.logging_controllers.values()))
        window.channel_panels[1].stop_logging_button.click()
        assert not window.logging_controllers[1].snapshot.active
        assert window.logging_controllers[2].snapshot.active
        window.close()
        assert not window.logging_controllers[2].snapshot.active
    finally:
        window.close()
        assert connection.shutdown(1)
