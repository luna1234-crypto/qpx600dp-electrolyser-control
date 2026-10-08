"""Milestone 9 Constant Current protocols using simulator/fake backends only."""

from collections.abc import Callable
import csv
import os
from pathlib import Path
from threading import current_thread
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.data.csv_logging import ChannelLoggingController, CsvLogFile
from qpx_electrolyser.engine.constant_current import (
    ConstantCurrentController,
    ProtocolState,
)
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 4) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for protocol state")


class RecordingSimulator(SimulatedPowerSupply):
    def __init__(self, *, delay_s: float = 0.001) -> None:
        super().__init__(SimulatorConfig(communication_delay_s=delay_s))
        self.calls: list[tuple[str, int, float | None, str]] = []

    def _record(self, name: str, channel: int, value: float | None = None) -> None:
        self.calls.append((name, channel, value, current_thread().name))

    def set_voltage(self, channel: int, voltage_v: float) -> None:
        self._record("set_voltage", channel, voltage_v)
        super().set_voltage(channel, voltage_v)

    def set_current(self, channel: int, current_a: float) -> None:
        self._record("set_current", channel, current_a)
        super().set_current(channel, current_a)

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
    qapp: QApplication, backend: PowerSupplyInterface | None = None,
) -> tuple[ConnectionController, PowerSupplyInterface]:
    selected = backend or RecordingSimulator()
    connection = ConnectionController(lambda: selected)
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    if isinstance(selected, RecordingSimulator):
        selected.calls.clear()
    return connection, selected


def start_protocol(
    controller: ConstantCurrentController,
    tmp_path: Path,
    *,
    duration: str = "0.35",
    interval: str = "0.1",
) -> bool:
    return controller.start("0.1", "2", duration, interval, "60 V", tmp_path)


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize("channel", [1, 2])
def test_normal_completion_for_each_channel(
    qapp: QApplication, tmp_path: Path, channel: int,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantCurrentController(channel, connection)
    try:
        assert start_protocol(protocol, tmp_path)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        assert protocol.snapshot.sample_count >= 2
        assert 0.35 <= protocol.snapshot.elapsed_active_s < 0.7
        assert protocol.snapshot.remaining_active_s == 0
        calls = [item for item in backend.calls if item[1] == channel]  # type: ignore[attr-defined]
        assert sum(item[0] == "output_on" for item in calls) == 1
        assert sum(item[0] == "output_off" for item in calls) == 1
        assert not any(item[1] != channel for item in backend.calls)  # type: ignore[attr-defined]
        data = csv_rows(protocol.snapshot.file_path)  # type: ignore[arg-type]
        samples = [row for row in data if row["sample_status"] == "ok"]
        assert samples
        assert {row["protocol_name"] for row in samples} == {"constant_current"}
        assert {row["protocol_state"] for row in samples} == {"RUNNING"}
    finally:
        assert connection.shutdown(1)


def test_start_command_order_and_shared_worker_thread(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantCurrentController(1, connection)
    try:
        assert start_protocol(protocol, tmp_path, duration="1")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        names = [item[0] for item in backend.calls[:10]]  # type: ignore[attr-defined]
        assert names == [
            "read_output_state",
            "set_voltage",
            "set_current",
            "read_set_voltage",
            "read_set_current",
            "read_output_state",
            "read_set_voltage",
            "read_set_current",
            "output_on",
            "read_output_state",
        ]
        assert {item[3] for item in backend.calls} == {"InstrumentCommandWorker"}  # type: ignore[attr-defined]
        protocol.stop()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
    finally:
        assert connection.shutdown(1)


def test_simultaneous_protocols_are_independent_and_serialized(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    protocols = [ConstantCurrentController(channel, connection) for channel in (1, 2)]
    try:
        assert all(start_protocol(item, tmp_path) for item in protocols)
        wait_until(qapp, lambda: all(
            item.snapshot.state is ProtocolState.COMPLETED for item in protocols
        ))
        assert all(item.snapshot.sample_count >= 1 for item in protocols)
        assert {call[1] for call in backend.calls} == {1, 2}  # type: ignore[attr-defined]
        assert {call[3] for call in backend.calls} == {"InstrumentCommandWorker"}  # type: ignore[attr-defined]
    finally:
        assert connection.shutdown(1)


@pytest.mark.parametrize(
    ("current", "voltage", "duration", "interval"),
    [
        ("", "2", "1", "0.1"),
        ("nan", "2", "1", "0.1"),
        ("0", "2", "1", "0.1"),
        ("5.01", "2", "1", "0.1"),
        ("0.1", "10.001", "1", "0.1"),
        ("0.1", "2", "0", "0.1"),
        ("0.1", "2", "1", "0"),
        ("0.1", "2", "1", "2"),
    ],
)
def test_invalid_inputs_are_rejected_before_queue_or_file(
    qapp: QApplication,
    tmp_path: Path,
    current: str,
    voltage: str,
    duration: str,
    interval: str,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantCurrentController(1, connection)
    try:
        assert not protocol.start(current, voltage, duration, interval, "60 V", tmp_path)
        assert protocol.snapshot.state is ProtocolState.FAILED
        assert not backend.calls  # type: ignore[attr-defined]
        assert not list(tmp_path.glob("*.csv"))
    finally:
        assert connection.shutdown(1)


def test_output_initially_on_is_rejected_without_mutation_or_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    ticket = connection.submit_command("fixture-on", lambda supply: supply.output_on(1))
    assert ticket is not None
    wait_until(qapp, ticket.done)
    backend.calls.clear()  # type: ignore[attr-defined]
    protocol = ConstantCurrentController(1, connection)
    try:
        assert start_protocol(protocol, tmp_path)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert [item[0] for item in backend.calls] == ["read_output_state"]  # type: ignore[attr-defined]
    finally:
        connection.submit_command("fixture-off", lambda supply: supply.output_off(1))
        assert connection.shutdown(1)


def test_readback_mismatch_fails_and_guarantees_output_off_attempt(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class MismatchSimulator(RecordingSimulator):
        armed = False

        def set_current(self, channel: int, current_a: float) -> None:
            super().set_current(channel, current_a)
            self.armed = True

        def read_set_current(self, channel: int) -> float:
            value = super().read_set_current(channel)
            return value + 0.02 if self.armed else value

    backend = MismatchSimulator()
    connection, _ = connected(qapp, backend)
    protocol = ConstantCurrentController(1, connection)
    try:
        assert start_protocol(protocol, tmp_path)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        names = [item[0] for item in backend.calls]
        assert "output_on" not in names
        assert names.count("output_off") == 1
    finally:
        assert connection.shutdown(1)


def test_failure_after_output_on_never_retries_and_still_attempts_off(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class RaiseAfterOnSimulator(RecordingSimulator):
        def output_on(self, channel: int) -> None:
            super().output_on(channel)
            raise RuntimeError("injected failure after Output On")

    backend = RaiseAfterOnSimulator()
    connection, _ = connected(qapp, backend)
    protocol = ConstantCurrentController(1, connection)
    try:
        assert start_protocol(protocol, tmp_path)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        names = [item[0] for item in backend.calls]
        assert names.count("output_on") == 1
        assert names.count("output_off") == 1
    finally:
        assert connection.shutdown(1)


def test_pause_resume_excludes_paused_time_and_stop_is_channel_local(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantCurrentController(1, connection)
    try:
        assert start_protocol(protocol, tmp_path, duration="0.6")
        wait_until(qapp, lambda: protocol.snapshot.sample_count >= 1)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.PAUSED)
        paused_elapsed = protocol.snapshot.elapsed_active_s
        time.sleep(0.25)
        qapp.processEvents()
        assert protocol.snapshot.elapsed_active_s == paused_elapsed
        protocol.resume()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        wait_until(qapp, lambda: protocol.snapshot.sample_count >= 2)
        protocol.stop("Early stop")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        names = [item[0] for item in backend.calls]  # type: ignore[attr-defined]
        assert names.count("output_on") == 2
        assert names.count("output_off") == 2
        assert all(item[1] == 1 for item in backend.calls)  # type: ignore[attr-defined]
    finally:
        assert connection.shutdown(1)


def test_unexpected_output_off_and_logging_failure_cleanup_without_retry(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class UnexpectedOffSimulator(RecordingSimulator):
        output_reads = 0

        def read_output_state(self, channel: int) -> bool:
            value = super().read_output_state(channel)
            self.output_reads += 1
            if self.output_reads >= 3:
                return False
            return value

    backend = UnexpectedOffSimulator()
    connection, _ = connected(qapp, backend)
    backend.output_reads = 0
    protocol = ConstantCurrentController(1, connection)
    try:
        assert start_protocol(protocol, tmp_path, duration="1")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        names = [item[0] for item in backend.calls]
        assert names.count("output_on") == 1
        assert names.count("output_off") == 1
    finally:
        assert connection.shutdown(1)


def test_malformed_sample_and_queue_rejection_each_trigger_cleanup(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qpx_electrolyser.engine.constant_current as module

    connection, backend = connected(qapp)
    protocol = ConstantCurrentController(1, connection)
    original_read_channel = module.read_channel
    malformed_reads = 0

    def malformed_sample_once(supply, channel):  # type: ignore[no-untyped-def]
        nonlocal malformed_reads
        malformed_reads += 1
        if malformed_reads == 1:
            return "malformed"
        return original_read_channel(supply, channel)

    monkeypatch.setattr(module, "read_channel", malformed_sample_once)
    try:
        assert start_protocol(protocol, tmp_path, duration="1")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert "Malformed protocol sample" in protocol.snapshot.message
        assert sum(item[0] == "output_on" for item in backend.calls) == 1  # type: ignore[attr-defined]
        assert sum(item[0] == "output_off" for item in backend.calls) == 1  # type: ignore[attr-defined]
    finally:
        assert connection.shutdown(1)

    monkeypatch.setattr(module, "read_channel", original_read_channel)
    connection2, backend2 = connected(qapp)
    protocol2 = ConstantCurrentController(1, connection2)
    original_submit = connection2.submit_command
    try:
        assert start_protocol(protocol2, tmp_path, duration="1")
        wait_until(qapp, lambda: protocol2.snapshot.state is ProtocolState.RUNNING)

        def reject_sample(name, operation, **kwargs):  # type: ignore[no-untyped-def]
            if name.endswith("-sample"):
                return None
            return original_submit(name, operation, **kwargs)

        monkeypatch.setattr(connection2, "submit_command", reject_sample)
        protocol2._sample_timer.start(0)
        wait_until(qapp, lambda: protocol2.snapshot.state is ProtocolState.FAILED)
        assert "queue rejected" in protocol2.snapshot.message.lower()
        assert sum(item[0] == "output_on" for item in backend2.calls) == 1  # type: ignore[attr-defined]
        assert sum(item[0] == "output_off" for item in backend2.calls) == 1  # type: ignore[attr-defined]
    finally:
        assert connection2.shutdown(1)


def test_direct_disconnect_reports_prominent_unconfirmed_off_warning(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, _backend = connected(qapp)
    protocol = ConstantCurrentController(1, connection)
    try:
        assert start_protocol(protocol, tmp_path, duration="1")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        connection.request_disconnect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.DISCONNECTED)
        assert protocol.snapshot.state is ProtocolState.FAILED
        assert protocol.snapshot.operator_warning
        assert "could not be confirmed" in protocol.snapshot.message
    finally:
        assert connection.shutdown(1)


def test_protocol_csv_failure_stops_output_and_closes_writer(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingWriter:
        def __init__(self, _directory: Path, channel: int) -> None:
            self.path = tmp_path / f"failure-{channel}.csv"
            self.closed = False

        def append(self, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            raise OSError("disk full")

        def close(self) -> None:
            self.closed = True

    backend = RecordingSimulator()
    connection, _ = connected(qapp, backend)
    protocol = ConstantCurrentController(1, connection, writer_factory=FailingWriter)
    try:
        assert start_protocol(protocol, tmp_path, duration="1")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert "CSV write failed" in protocol.snapshot.message
        assert sum(item[0] == "output_on" for item in backend.calls) == 1
        assert sum(item[0] == "output_off" for item in backend.calls) == 1
    finally:
        assert connection.shutdown(1)


def test_disconnect_marks_unconfirmed_off_and_shutdown_attempts_off(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantCurrentController(1, connection)
    try:
        assert start_protocol(protocol, tmp_path, duration="1")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.shutdown()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert any(item[0] == "output_off" for item in backend.calls)  # type: ignore[attr-defined]
    finally:
        assert connection.shutdown(1)


def test_gui_disables_only_owned_channel_and_coordinates_disconnect(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: QMessageBox.StandardButton.Ok)
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show()
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        panel = window.protocol_panels[1]
        panel.duration_input.setText("1")
        panel.interval_input.setText("0.1")
        panel.start_button.click()
        wait_until(qapp, lambda: window.protocol_controllers[1].snapshot.state is ProtocolState.RUNNING)
        assert not window.channel_panels[1].refresh_button.isEnabled()
        assert window.channel_panels[2].refresh_button.isEnabled()
        assert not window.channel_panels[1].start_logging_button.isEnabled()
        window._request_disconnect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.DISCONNECTED)
        assert any(item[0] == "output_off" and item[1] == 1 for item in backend.calls)
    finally:
        window.close()
        assert connection.shutdown(1)


def test_active_manual_logger_keeps_protocol_start_enabled_and_owns_single_csv(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show()
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        window.logging_interval.setValue(0.1)
        window.channel_panels[1].start_logging_button.click()
        logger = window.logging_controllers[1]
        wait_until(qapp, lambda: logger.snapshot.sample_count >= 1)
        shared_path = logger.snapshot.file_path

        panel = window.protocol_panels[1]
        assert panel.start_button.isEnabled()
        panel.duration_input.setText("1")
        panel.interval_input.setText("0.1")
        panel.start_button.click()
        protocol = window.protocol_controllers[1]
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        wait_until(qapp, lambda: protocol.snapshot.sample_count >= 1)
        assert protocol.snapshot.file_path == shared_path

        panel.pause_button.click()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.PAUSED)
        panel.resume_button.click()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        wait_until(qapp, lambda: protocol.snapshot.sample_count >= 2)
        panel.stop_button.click()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)

        assert logger.snapshot.active
        panel.duration_input.setText("0.35")
        panel.start_button.click()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        assert logger.snapshot.active
        assert protocol.snapshot.file_path == shared_path

        logger.stop("Operator stopped logging after protocol")
        rows = csv_rows(shared_path)  # type: ignore[arg-type]
        protocol_rows = [row for row in rows if row["protocol_name"] == "constant_current"]
        assert protocol_rows
        assert {row["protocol_state"] for row in protocol_rows} >= {"RUNNING", "PAUSED"}
        assert any(not row["protocol_name"] for row in rows)
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
    finally:
        protocol = window.protocol_controllers[1]
        if protocol.snapshot.owns_channel:
            protocol.stop("Regression test cleanup")
            wait_until(qapp, lambda: not protocol.snapshot.owns_channel)
        window.logging_controllers[1].shutdown()
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)


def test_active_logger_file_failure_safely_stops_protocol_without_second_writer(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailProtocolRowsWriter:
        def __init__(self, directory: Path, channel: int) -> None:
            self._inner = CsvLogFile(directory, channel)
            self.path = self._inner.path
            self.closed = False

        def append(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            if kwargs.get("protocol_name"):
                raise OSError("injected shared protocol-log failure")
            self._inner.append(**kwargs)

        def close(self) -> None:
            self.closed = True
            self._inner.close()

    created: list[FailProtocolRowsWriter] = []

    def logger_writer_factory(directory: Path, channel: int) -> FailProtocolRowsWriter:
        writer = FailProtocolRowsWriter(directory, channel)
        created.append(writer)
        return writer

    def reject_second_writer(_directory: Path, _channel: int):  # type: ignore[no-untyped-def]
        raise AssertionError("protocol attempted to create a second CSV writer")

    backend = RecordingSimulator()
    connection, _ = connected(qapp, backend)
    logger = ChannelLoggingController(1, connection, writer_factory=logger_writer_factory)
    protocol = ConstantCurrentController(1, connection, writer_factory=reject_second_writer)
    logger.snapshot_changed.connect(protocol.logging_snapshot_changed)
    try:
        assert logger.start(tmp_path, 0.1)
        wait_until(qapp, lambda: logger.snapshot.sample_count >= 1)
        assert protocol.start(
            "0.1",
            "2",
            "1",
            "0.1",
            "60 V",
            tmp_path,
            logging_sink=logger,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)

        assert not logger.snapshot.active
        assert "logging" in protocol.snapshot.message.lower()
        assert sum(item[0] == "output_on" for item in backend.calls) == 1
        assert sum(item[0] == "output_off" for item in backend.calls) == 1
        assert len(created) == 1
        assert created[0].closed
    finally:
        logger.shutdown()
        assert connection.shutdown(1)


def test_pause_publishes_confirmed_off_readback_to_every_indicator(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show()
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        panel = window.protocol_panels[1]
        panel.duration_input.setText("2")
        panel.interval_input.setText("0.1")
        panel.start_button.click()
        wait_until(qapp, lambda: window.channel_controllers[1].snapshot.output_enabled is True)

        panel.pause_button.click()
        wait_until(
            qapp,
            lambda: window.protocol_controllers[1].snapshot.state is ProtocolState.PAUSED,
        )

        assert backend._channels[0].output_enabled is False
        assert window.channel_controllers[1].snapshot.output_enabled is False
        assert window.channel_panels[1].output_state_value.text() == "OUTPUT OFF"
        assert window.protocol_controllers[1].snapshot.output_enabled is False
        assert window.protocol_panels[1].output_state_value.text() == "OUTPUT OFF"
    finally:
        protocol = window.protocol_controllers[1]
        if protocol.snapshot.owns_channel:
            protocol.stop("Regression test cleanup")
            wait_until(qapp, lambda: not protocol.snapshot.owns_channel)
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)


def test_pause_never_enters_paused_until_output_off_is_confirmed(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FirstOffAttemptNotAppliedSimulator(RecordingSimulator):
        off_attempts = 0

        def output_off(self, channel: int) -> None:
            self._record("output_off", channel)
            self.off_attempts += 1
            if self.off_attempts > 1:
                SimulatedPowerSupply.output_off(self, channel)

    backend = FirstOffAttemptNotAppliedSimulator()
    connection, _ = connected(qapp, backend)
    protocol = ConstantCurrentController(1, connection)
    states: list[ProtocolState] = []
    protocol.snapshot_changed.connect(lambda snapshot: states.append(snapshot.state))
    try:
        assert start_protocol(protocol, tmp_path, duration="2")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)

        assert ProtocolState.PAUSED not in states
        assert backend.off_attempts == 2
        assert backend._channels[0].output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_window_close_during_protocol_cleans_output_and_exits_without_residual_work(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show()
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        panel = window.protocol_panels[1]
        panel.duration_input.setText("1")
        panel.interval_input.setText("0.1")
        panel.start_button.click()
        wait_until(qapp, lambda: window.protocol_controllers[1].snapshot.state is ProtocolState.RUNNING)
        assert window.protocol_panels[1].state_value.text() == "RUNNING"
        window.close()
        wait_until(qapp, lambda: not window.isVisible())
        assert any(item[0] == "output_off" and item[1] == 1 for item in backend.calls)
        assert not window.protocol_controllers[1].snapshot.owns_channel
    finally:
        window.close()
        assert connection.shutdown(1)
