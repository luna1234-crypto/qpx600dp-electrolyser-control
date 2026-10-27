"""Milestone 13 stepped Current Scan tests using simulator/fake backends only."""

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
    CurrentScanController,
    generate_inclusive_setpoints,
)
from qpx_electrolyser.gui.current_scan_panel import CurrentScanPanel
from qpx_electrolyser.gui.main_window import MainWindow
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
    raise AssertionError("Timed out waiting for Current Scan state")


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
    controller: CurrentScanController,
    directory: Path,
    *,
    start: str = "0.1",
    stop: str = "0.3",
    step: str = "0.1",
    voltage: str = "2",
    dwell: str = "0.2",
    interval: str = "0.1",
    logging_sink=None,  # type: ignore[no-untyped-def]
) -> bool:
    return controller.start(
        start, stop, step, voltage, dwell, interval, "60 V", directory,
        logging_sink=logging_sink,
    )


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_current_setpoints_are_exact_inclusive_and_non_divisible() -> None:
    assert generate_inclusive_setpoints(
        "0.1", "0.5", "0.1", axis_label="Current"
    ) == tuple(Decimal(value) for value in ("0.1", "0.2", "0.3", "0.4", "0.5"))
    assert generate_inclusive_setpoints(
        "0.3", "0.1", "-0.08", axis_label="Current"
    ) == tuple(Decimal(value) for value in ("0.3", "0.22", "0.14", "0.1"))


@pytest.mark.parametrize(
    ("start", "stop", "step", "voltage", "dwell", "interval"),
    [
        ("0.1", "0.3", "0", "2", "1", "0.1"),
        ("0.1", "0.3", "0.001", "2", "1", "0.1"),
        ("0.1", "0.3", "-0.1", "2", "1", "0.1"),
        ("0.3", "0.1", "0.1", "2", "1", "0.1"),
        ("nan", "0.3", "0.1", "2", "1", "0.1"),
        ("0.1", "5.01", "0.1", "2", "1", "0.1"),
        ("0.1", "0.3", "0.1", "10.001", "1", "0.1"),
        ("0.1", "0.3", "0.1", "2", "0", "0.1"),
        ("0.1", "0.3", "0.1", "2", "1", "2"),
    ],
)
def test_invalid_inputs_precede_file_queue_and_output_mutation(
    qapp: QApplication,
    tmp_path: Path,
    start: str,
    stop: str,
    step: str,
    voltage: str,
    dwell: str,
    interval: str,
) -> None:
    connection, backend = connected(qapp)
    scan = CurrentScanController(1, connection)
    try:
        assert not scan.start(
            start, stop, step, voltage, dwell, interval, "60 V", tmp_path
        )
        assert scan.snapshot.state is ProtocolState.FAILED
        assert backend.calls == []
        assert not list(tmp_path.glob("*.csv"))
    finally:
        assert connection.shutdown(1)


@pytest.mark.parametrize(
    ("start", "stop", "step", "expected"),
    [
        ("0.1", "0.3", "0.1", [0.1, 0.2, 0.3]),
        ("0.3", "0.1", "-0.1", [0.3, 0.2, 0.1]),
        ("0.3", "0.1", "-0.08", [0.3, 0.22, 0.14, 0.1]),
    ],
)
def test_command_order_steps_csv_and_safe_completion(
    qapp: QApplication,
    tmp_path: Path,
    start: str,
    stop: str,
    step: str,
    expected: list[float],
) -> None:
    connection, backend = connected(qapp)
    scan = CurrentScanController(1, connection)
    try:
        assert start_scan(scan, tmp_path, start=start, stop=stop, step=step)
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        names = [call[0] for call in backend.calls]
        assert names[:10] == [
            "read_output_state", "set_voltage", "set_current",
            "read_set_voltage", "read_set_current", "read_output_state",
            "read_set_voltage", "read_set_current", "output_on", "read_output_state",
        ]
        assert [call[2] for call in backend.calls if call[0] == "set_current"] == expected
        assert names.count("set_voltage") == 1
        assert names.count("output_on") == 1
        assert names.count("output_off") == 1
        assert {call[1] for call in backend.calls} == {1}
        assert {call[3] for call in backend.calls} == {"InstrumentCommandWorker"}
        data = rows(scan.snapshot.file_path)  # type: ignore[arg-type]
        scan_rows = [row for row in data if row["scan_name"] == "current_scan"]
        assert scan_rows
        assert {int(row["scan_total_steps"]) for row in scan_rows} == {len(expected)}
        assert {float(row["scan_commanded_setpoint"]) for row in scan_rows} == set(expected)
        assert [int(row["scan_step_index"]) for row in scan_rows] == sorted(
            int(row["scan_step_index"]) for row in scan_rows
        )
        assert scan.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_setpoint_readback_mismatch_fails_with_one_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class MismatchSimulator(RecordingSimulator):
        writes = 0

        def set_current(self, channel: int, value: float) -> None:
            self.writes += 1
            super().set_current(channel, value)

        def read_set_current(self, channel: int) -> float:
            value = super().read_set_current(channel)
            return value + 0.01 if self.writes >= 2 else value

    backend = MismatchSimulator()
    connection, _ = connected(qapp, backend)
    scan = CurrentScanController(1, connection)
    try:
        assert start_scan(scan, tmp_path)
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.FAILED)
        assert "mismatch" in scan.snapshot.message
        assert [call[0] for call in backend.calls].count("output_on") == 1
        assert [call[0] for call in backend.calls].count("output_off") == 1
        assert scan.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_pause_resume_keeps_step_file_and_excludes_paused_time(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    scan = CurrentScanController(1, connection)
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
        assert scan.resume()
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        assert scan.snapshot.file_path == path
        assert [call[2] for call in backend.calls if call[0] == "set_current"] == [
            0.1, 0.2, 0.3,
        ]
        assert [call[0] for call in backend.calls].count("output_on") == 2
        assert [call[0] for call in backend.calls].count("output_off") == 2
        assert any(row["sample_status"] == "paused" for row in rows(path))  # type: ignore[arg-type]
    finally:
        assert connection.shutdown(1)


def test_simultaneous_scans_are_isolated_serialized_and_count_missed_slots(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp, RecordingSimulator(delay_s=0.02))
    scans = {channel: CurrentScanController(channel, connection) for channel in (1, 2)}
    try:
        assert all(start_scan(scan, tmp_path, dwell="0.6") for scan in scans.values())
        wait_until(qapp, lambda: all(
            scan.snapshot.state is ProtocolState.COMPLETED for scan in scans.values()
        ), 10)
        assert {call[1] for call in backend.calls} == {1, 2}
        assert {call[3] for call in backend.calls} == {"InstrumentCommandWorker"}
        assert any(scan.snapshot.missed_samples >= 1 for scan in scans.values())
        for channel, scan in scans.items():
            assert {row["channel"] for row in rows(scan.snapshot.file_path)} == {  # type: ignore[arg-type]
                str(channel)
            }
    finally:
        assert connection.shutdown(1)


def test_gui_reuses_logger_and_plot_flow_and_disables_real_hardware(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController(lambda: RecordingSimulator())
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert window.logging_controllers[1].start(tmp_path, 0.1)
        log_path = window.logging_controllers[1].snapshot.file_path
        panel = window.current_scan_panels[1]
        panel.start_current_input.setText("0.1")
        panel.stop_current_input.setText("0.2")
        panel.step_input.setText("0.1")
        panel.dwell_input.setText("0.2")
        panel.interval_input.setText("0.1")
        panel.start_button.click()
        scan = window.current_scan_controllers[1]
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        assert scan.snapshot.file_path == log_path
        assert window.logging_controllers[1].snapshot.active
        assert window.plot_controllers[1].snapshot.total_samples >= 1
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
        window.logging_controllers[1].stop("current scan integration complete")
        assert any(row["scan_name"] == "current_scan" for row in rows(log_path))  # type: ignore[arg-type]

        isolated_panel = CurrentScanPanel(1)
        isolated_panel.set_context(available=True, simulation_mode=False)
        assert not isolated_panel.start_button.isEnabled()
        assert "Milestone 13" in isolated_panel.message_label.text()
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)


def test_current_scan_ownership_disables_only_same_channel_controls(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController(lambda: RecordingSimulator())
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        scan = window.current_scan_controllers[1]
        assert start_scan(scan, tmp_path, dwell="1")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        assert not window.channel_panels[1].voltage_input.isEnabled()
        assert not window.protocol_panels[1].start_button.isEnabled()
        assert not window.voltage_protocol_panels[1].start_button.isEnabled()
        assert not window.voltage_scan_panels[1].start_button.isEnabled()
        assert window.channel_panels[2].voltage_input.isEnabled()
        assert window.current_scan_panels[2].start_button.isEnabled()
        scan.stop("ownership test complete")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)


@pytest.mark.parametrize("action", ["stop", "shutdown"])
def test_stop_and_shutdown_each_use_one_safe_cleanup(
    qapp: QApplication, tmp_path: Path, action: str,
) -> None:
    connection, backend = connected(qapp)
    scan = CurrentScanController(1, connection)
    try:
        assert start_scan(scan, tmp_path, dwell="1")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        if action == "stop":
            scan.stop("operator stop test")
            terminal = ProtocolState.COMPLETED
        else:
            scan.shutdown()
            terminal = ProtocolState.FAILED
        wait_until(qapp, lambda: scan.snapshot.state is terminal)
        assert scan.snapshot.output_enabled is False
        assert [call[0] for call in backend.calls].count("output_on") == 1
        assert [call[0] for call in backend.calls].count("output_off") == 1
        count = len(backend.calls)
        time.sleep(0.2)
        qapp.processEvents()
        assert len(backend.calls) == count
    finally:
        assert connection.shutdown(1)


def test_logging_failure_duplicate_stop_and_stale_callbacks_are_safe(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingWriter:
        def __init__(self, _directory: Path, channel: int) -> None:
            self.path = tmp_path / f"failing-current-scan-{channel}.csv"

        def append(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            if kwargs.get("status") == "ok":
                raise OSError("injected current scan disk failure")

        def close(self) -> None:
            pass

    connection, backend = connected(qapp)
    scan = CurrentScanController(1, connection, writer_factory=FailingWriter)
    try:
        assert start_scan(scan, tmp_path, dwell="1")
        assert not start_scan(scan, tmp_path, dwell="1")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.FAILED)
        scan.stop("ignored after failure")
        count = len(backend.calls)
        time.sleep(0.2)
        qapp.processEvents()
        assert len(backend.calls) == count
        assert [call[0] for call in backend.calls].count("output_on") == 1
        assert [call[0] for call in backend.calls].count("output_off") == 1
        assert scan.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_disconnect_and_shutdown_use_existing_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        scan = window.current_scan_controllers[2]
        assert start_scan(scan, tmp_path, dwell="1")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        window._request_disconnect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.DISCONNECTED)
        assert scan.snapshot.output_enabled is False
        assert [call[0] for call in backend.calls].count("output_off") == 1
        count = len(backend.calls)
        time.sleep(0.2)
        qapp.processEvents()
        assert len(backend.calls) == count
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)
