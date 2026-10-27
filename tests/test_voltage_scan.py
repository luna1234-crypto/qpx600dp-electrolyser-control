"""Milestone 12 stepped Voltage Scan tests using simulator/fake backends only."""

from collections.abc import Callable
import csv
from decimal import Decimal
import os
from pathlib import Path
from threading import current_thread
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.engine.stepped_scan import (
    VoltageScanController,
    generate_inclusive_setpoints,
)
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.gui.voltage_scan_panel import VoltageScanPanel
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 5) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for Voltage Scan state")


class RecordingSimulator(SimulatedPowerSupply):
    def __init__(self, delay_s: float = 0.001) -> None:
        super().__init__(SimulatorConfig(communication_delay_s=delay_s))
        self.calls: list[tuple[str, int, float | None, str]] = []

    def _record(self, name: str, channel: int, value: float | None = None) -> None:
        self.calls.append((name, channel, value, current_thread().name))

    def set_voltage(self, channel: int, value: float) -> None:
        self._record("set_voltage", channel, value)
        super().set_voltage(channel, value)

    def set_current(self, channel: int, value: float) -> None:
        self._record("set_current", channel, value)
        super().set_current(channel, value)

    def read_set_voltage(self, channel: int) -> float:
        self._record("read_set_voltage", channel)
        return super().read_set_voltage(channel)

    def read_set_current(self, channel: int) -> float:
        self._record("read_set_current", channel)
        return super().read_set_current(channel)

    def read_voltage_range(self, channel: int):  # type: ignore[no-untyped-def]
        self._record("read_voltage_range", channel)
        return super().read_voltage_range(channel)

    def read_voltage(self, channel: int) -> float:
        self._record("read_voltage", channel)
        return super().read_voltage(channel)

    def read_current(self, channel: int) -> float:
        self._record("read_current", channel)
        return super().read_current(channel)

    def output_on(self, channel: int) -> None:
        self._record("output_on", channel)
        super().output_on(channel)

    def output_off(self, channel: int) -> None:
        self._record("output_off", channel)
        super().output_off(channel)

    def read_output_state(self, channel: int) -> bool:
        self._record("read_output_state", channel)
        return super().read_output_state(channel)


def connected(
    qapp: QApplication, backend: RecordingSimulator | None = None,
) -> tuple[ConnectionController, RecordingSimulator]:
    selected = backend or RecordingSimulator()
    connection = ConnectionController(lambda: selected)
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    selected.calls.clear()
    return connection, selected


def start_scan(
    controller: VoltageScanController,
    directory: Path,
    *,
    start: str = "1",
    stop: str = "2",
    step: str = "0.5",
    dwell: str = "0.2",
    interval: str = "0.1",
    logging_sink=None,  # type: ignore[no-untyped-def]
) -> bool:
    return controller.start(
        start, stop, step, "0.1", dwell, interval, "60 V", directory,
        logging_sink=logging_sink,
    )


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_exact_inclusive_setpoint_generation_has_no_drift_or_overshoot() -> None:
    assert generate_inclusive_setpoints("0.1", "0.5", "0.1") == tuple(
        Decimal(value) for value in ("0.1", "0.2", "0.3", "0.4", "0.5")
    )
    assert generate_inclusive_setpoints("0", "1", "0.3") == tuple(
        Decimal(value) for value in ("0", "0.3", "0.6", "0.9", "1")
    )
    assert generate_inclusive_setpoints("1", "0", "-0.3") == tuple(
        Decimal(value) for value in ("1", "0.7", "0.4", "0.1", "0")
    )
    assert generate_inclusive_setpoints("2", "2", "0.1") == (Decimal("2"),)


@pytest.mark.parametrize(
    ("start", "stop", "step", "current", "dwell", "interval"),
    [
        ("1", "2", "0", "0.1", "1", "0.1"),
        ("1", "2", "0.0001", "0.1", "1", "0.1"),
        ("1", "2", "-0.1", "0.1", "1", "0.1"),
        ("2", "1", "0.1", "0.1", "1", "0.1"),
        ("nan", "2", "0.1", "0.1", "1", "0.1"),
        ("1", "11", "1", "0.1", "1", "0.1"),
        ("1", "2", "0.1", "5.01", "1", "0.1"),
        ("1", "2", "0.1", "0.1", "0", "0.1"),
        ("1", "2", "0.1", "0.1", "1", "2"),
    ],
)
def test_invalid_inputs_precede_file_queue_and_output_mutation(
    qapp: QApplication,
    tmp_path: Path,
    start: str,
    stop: str,
    step: str,
    current: str,
    dwell: str,
    interval: str,
) -> None:
    connection, backend = connected(qapp)
    scan = VoltageScanController(1, connection)
    try:
        assert not scan.start(
            start, stop, step, current, dwell, interval, "60 V", tmp_path
        )
        assert scan.snapshot.state is ProtocolState.FAILED
        assert backend.calls == []
        assert not list(tmp_path.glob("*.csv"))
    finally:
        assert connection.shutdown(1)


@pytest.mark.parametrize(
    ("start", "stop", "step", "expected"),
    [
        ("1", "2", "0.5", [1.0, 1.5, 2.0]),
        ("2", "1", "-0.5", [2.0, 1.5, 1.0]),
        ("1", "2", "0.4", [1.0, 1.4, 1.8, 2.0]),
    ],
)
def test_scan_command_order_steps_csv_and_safe_completion(
    qapp: QApplication,
    tmp_path: Path,
    start: str,
    stop: str,
    step: str,
    expected: list[float],
) -> None:
    connection, backend = connected(qapp)
    scan = VoltageScanController(1, connection)
    try:
        assert start_scan(scan, tmp_path, start=start, stop=stop, step=step)
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        names = [call[0] for call in backend.calls]
        assert names[:10] == [
            "read_output_state", "set_voltage", "set_current",
            "read_set_voltage", "read_set_current", "read_output_state",
            "read_set_voltage", "read_set_current", "output_on", "read_output_state",
        ]
        programmed = [call[2] for call in backend.calls if call[0] == "set_voltage"]
        assert programmed == expected
        assert names.count("output_on") == 1
        assert names.count("output_off") == 1
        assert {call[1] for call in backend.calls} == {1}
        assert {call[3] for call in backend.calls} == {"InstrumentCommandWorker"}
        data = rows(scan.snapshot.file_path)  # type: ignore[arg-type]
        scan_rows = [row for row in data if row["scan_name"] == "voltage_scan"]
        assert scan_rows
        assert {int(row["scan_total_steps"]) for row in scan_rows} == {len(expected)}
        assert {float(row["scan_commanded_setpoint"]) for row in scan_rows} == set(expected)
        indices = [int(row["scan_step_index"]) for row in scan_rows]
        assert indices == sorted(indices)
        assert scan.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_pause_resume_preserves_current_step_csv_and_excludes_paused_time(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    scan = VoltageScanController(1, connection)
    try:
        assert start_scan(scan, tmp_path, dwell="0.5")
        wait_until(qapp, lambda: scan.snapshot.sample_count >= 1)
        path = scan.snapshot.file_path
        step = scan.snapshot.scan_step_index
        scan.pause()
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.PAUSED)
        paused_elapsed = scan.snapshot.elapsed_active_s
        time.sleep(0.2)
        qapp.processEvents()
        assert scan.snapshot.elapsed_active_s == paused_elapsed
        assert scan.snapshot.scan_step_index == step
        assert scan.snapshot.output_enabled is False
        assert scan.resume()
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        assert scan.snapshot.file_path == path
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        assert [call[0] for call in backend.calls].count("output_on") == 2
        assert [call[0] for call in backend.calls].count("output_off") == 2
        data = rows(path)  # type: ignore[arg-type]
        assert any(row["sample_status"] == "paused" for row in data)
        assert len({row["scan_step_index"] for row in data if row["scan_step_index"]}) >= 2
    finally:
        assert connection.shutdown(1)


def test_simultaneous_scans_are_channel_isolated_and_serialized(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    scans = {channel: VoltageScanController(channel, connection) for channel in (1, 2)}
    try:
        assert all(start_scan(scan, tmp_path) for scan in scans.values())
        wait_until(qapp, lambda: all(
            scan.snapshot.state is ProtocolState.COMPLETED for scan in scans.values()
        ))
        assert {call[1] for call in backend.calls} == {1, 2}
        assert {call[3] for call in backend.calls} == {"InstrumentCommandWorker"}
        for channel, scan in scans.items():
            data = rows(scan.snapshot.file_path)  # type: ignore[arg-type]
            assert {row["channel"] for row in data} == {str(channel)}
    finally:
        assert connection.shutdown(1)


def test_step_failure_uses_existing_cleanup_once_and_never_mutates_other_channel(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingStepSimulator(RecordingSimulator):
        writes = 0

        def set_voltage(self, channel: int, value: float) -> None:
            self._record("set_voltage", channel, value)
            self.writes += 1
            if self.writes == 2:
                raise RuntimeError("injected step failure")
            SimulatedPowerSupply.set_voltage(self, channel, value)

    backend = FailingStepSimulator()
    connection, _ = connected(qapp, backend)
    scan = VoltageScanController(1, connection)
    try:
        assert start_scan(scan, tmp_path)
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.FAILED)
        assert [call[0] for call in backend.calls].count("output_on") == 1
        assert [call[0] for call in backend.calls].count("output_off") == 1
        assert {call[1] for call in backend.calls} == {1}
        assert scan.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_gui_reuses_one_logger_plot_flow_and_enables_guarded_real_hardware_panel(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController(lambda: RecordingSimulator())
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert window.logging_controllers[1].start(tmp_path, 0.1)
        log_path = window.logging_controllers[1].snapshot.file_path
        panel = window.voltage_scan_panels[1]
        panel.start_voltage_input.setText("1")
        panel.stop_voltage_input.setText("1.5")
        panel.step_input.setText("0.5")
        panel.dwell_input.setText("0.2")
        panel.interval_input.setText("0.1")
        panel.start_button.click()
        scan = window.voltage_scan_controllers[1]
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        assert scan.snapshot.file_path == log_path
        assert window.logging_controllers[1].snapshot.active
        assert window.plot_controllers[1].snapshot.total_samples >= 1
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
        window.logging_controllers[1].stop("scan integration test complete")
        data = rows(log_path)  # type: ignore[arg-type]
        assert any(row["scan_name"] == "voltage_scan" for row in data)

        isolated_panel = VoltageScanPanel(1)
        isolated_panel.set_context(available=True, simulation_mode=False)
        assert isolated_panel.start_button.isEnabled()
        assert "physical acceptance pending" in isolated_panel.message_label.text()
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)


def test_shutdown_cleans_output_and_cancels_later_scan_callbacks(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    scan = VoltageScanController(2, connection)
    try:
        assert start_scan(scan, tmp_path, dwell="1")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        scan.shutdown()
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.FAILED)
        count = len(backend.calls)
        time.sleep(0.25)
        qapp.processEvents()
        assert len(backend.calls) == count
        assert scan.snapshot.output_enabled is False
        assert [call[0] for call in backend.calls].count("output_off") == 1
    finally:
        assert connection.shutdown(1)


def test_slow_samples_count_missed_intervals_without_overlap(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp, RecordingSimulator(delay_s=0.03))
    scan = VoltageScanController(1, connection)
    try:
        assert start_scan(
            scan, tmp_path, start="1", stop="1", step="0.1",
            dwell="0.6", interval="0.1",
        )
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED, 8)
        assert scan.snapshot.missed_samples >= 1
        assert {call[3] for call in backend.calls} == {"InstrumentCommandWorker"}
    finally:
        assert connection.shutdown(1)


def test_pause_at_new_step_resumes_without_duplicate_or_skipped_setpoint(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    scan = VoltageScanController(1, connection)
    try:
        assert start_scan(scan, tmp_path, dwell="0.3")
        wait_until(qapp, lambda: scan.snapshot.scan_step_index == 2)
        scan.pause()
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.PAUSED)
        assert scan.snapshot.scan_commanded_setpoint == 1.5
        assert scan.resume()
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        assert [call[2] for call in backend.calls if call[0] == "set_voltage"] == [
            1.0, 1.5, 2.0,
        ]
    finally:
        assert connection.shutdown(1)


def test_logging_failure_stops_scan_with_one_safe_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingWriter:
        def __init__(self, _directory: Path, channel: int) -> None:
            self.path = tmp_path / f"failing-scan-{channel}.csv"
            self.closed = False

        def append(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            if kwargs.get("status") == "ok":
                raise OSError("injected scan disk failure")

        def close(self) -> None:
            self.closed = True

    connection, backend = connected(qapp)
    scan = VoltageScanController(1, connection, writer_factory=FailingWriter)
    try:
        assert start_scan(scan, tmp_path, dwell="1")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.FAILED)
        assert "disk failure" in scan.snapshot.message
        assert [call[0] for call in backend.calls].count("output_on") == 1
        assert [call[0] for call in backend.calls].count("output_off") == 1
        assert scan.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_duplicate_start_and_late_callbacks_cannot_repeat_output_commands(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    scan = VoltageScanController(1, connection)
    try:
        assert start_scan(scan, tmp_path, dwell="0.4")
        assert not start_scan(scan, tmp_path, dwell="0.4")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        scan.stop("duplicate guard test")
        scan.stop("ignored repeated stop")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        count = len(backend.calls)
        time.sleep(0.2)
        qapp.processEvents()
        assert len(backend.calls) == count
        assert [call[0] for call in backend.calls].count("output_on") == 1
        assert [call[0] for call in backend.calls].count("output_off") == 1
    finally:
        assert connection.shutdown(1)


def test_coordinated_disconnect_uses_scan_cleanup_before_worker_disconnect(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        scan = window.voltage_scan_controllers[2]
        assert start_scan(scan, tmp_path, dwell="1")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        window._request_disconnect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.DISCONNECTED)
        assert scan.snapshot.output_enabled is False
        assert [call[0] for call in backend.calls].count("output_off") == 1
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)
