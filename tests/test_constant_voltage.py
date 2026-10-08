"""Milestone 11 Constant Voltage protocols use simulator/fake backends only."""

from collections.abc import Callable
import csv
import os
from pathlib import Path
from threading import current_thread
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.engine.constant_voltage import ConstantVoltageController
from qpx_electrolyser.gui.main_window import MainWindow
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
    raise AssertionError("Timed out waiting for Constant Voltage state")


class RecordingSimulator(SimulatedPowerSupply):
    def __init__(self) -> None:
        super().__init__(SimulatorConfig(communication_delay_s=0.001))
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

    def output_on(self, channel: int) -> None:
        self._record("output_on", channel)
        super().output_on(channel)

    def output_off(self, channel: int) -> None:
        self._record("output_off", channel)
        super().output_off(channel)

    def read_output_state(self, channel: int) -> bool:
        self._record("read_output_state", channel)
        return super().read_output_state(channel)


def connected(qapp: QApplication) -> tuple[ConnectionController, RecordingSimulator]:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    backend.calls.clear()
    return connection, backend


def start(controller: ConstantVoltageController, directory: Path, duration: str = "0.35") -> bool:
    return controller.start("2", "0.1", duration, "0.1", "60 V", directory)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize("channel", [1, 2])
def test_each_channel_completes_with_exact_start_and_safe_stop_order(
    qapp: QApplication, tmp_path: Path, channel: int,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantVoltageController(channel, connection)
    try:
        assert protocol.validate_inputs("8", "0.1", "1", "0.1", "60 V") is None
        assert start(protocol, tmp_path)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        names = [call[0] for call in backend.calls]
        assert names[:10] == [
            "read_output_state", "set_voltage", "set_current",
            "read_set_voltage", "read_set_current", "read_output_state",
            "read_set_voltage", "read_set_current", "output_on", "read_output_state",
        ]
        assert names.count("output_on") == 1
        assert names.count("output_off") == 1
        assert {call[1] for call in backend.calls} == {channel}
        assert {call[3] for call in backend.calls} == {"InstrumentCommandWorker"}
        samples = [row for row in rows(protocol.snapshot.file_path)
                   if row["sample_status"] == "ok"]  # type: ignore[arg-type]
        assert samples
        assert {row["protocol_name"] for row in samples} == {"constant_voltage"}
    finally:
        assert connection.shutdown(1)


@pytest.mark.parametrize(
    ("voltage", "current", "duration", "interval"),
    [("", "0.1", "1", "0.1"), ("nan", "0.1", "1", "0.1"),
     ("2", "-1", "1", "0.1"), ("2", "0.1", "0", "0.1"),
     ("2", "0.1", "1", "2")],
)
def test_invalid_inputs_have_no_file_or_queue_side_effect(
    qapp: QApplication, tmp_path: Path,
    voltage: str, current: str, duration: str, interval: str,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantVoltageController(1, connection)
    try:
        assert not protocol.start(
            voltage, current, duration, interval, "60 V", tmp_path
        )
        assert protocol.snapshot.state is ProtocolState.FAILED
        assert backend.calls == []
        assert not list(tmp_path.glob("*.csv"))
    finally:
        assert connection.shutdown(1)


def test_simultaneous_channels_share_queue_and_remain_isolated(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    protocols = {
        channel: ConstantVoltageController(channel, connection) for channel in (1, 2)
    }
    try:
        assert all(start(protocol, tmp_path) for protocol in protocols.values())
        wait_until(qapp, lambda: all(
            protocol.snapshot.state is ProtocolState.COMPLETED
            for protocol in protocols.values()
        ))
        assert sum(call[0] == "output_on" for call in backend.calls) == 2
        assert sum(call[0] == "output_off" for call in backend.calls) == 2
        assert {call[1] for call in backend.calls} == {1, 2}
        assert {call[3] for call in backend.calls} == {"InstrumentCommandWorker"}
    finally:
        assert connection.shutdown(1)


def test_pause_resume_and_stop_confirm_off_without_counting_pause(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantVoltageController(1, connection)
    try:
        assert start(protocol, tmp_path, "1")
        wait_until(qapp, lambda: protocol.snapshot.sample_count >= 1)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.PAUSED)
        paused_elapsed = protocol.snapshot.elapsed_active_s
        time.sleep(0.2)
        qapp.processEvents()
        assert protocol.snapshot.elapsed_active_s == paused_elapsed
        assert protocol.snapshot.output_enabled is False
        assert protocol.resume()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.stop("CV early stop")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        assert protocol.snapshot.output_enabled is False
        assert sum(call[0] == "output_on" for call in backend.calls) == 2
        assert sum(call[0] == "output_off" for call in backend.calls) == 2
    finally:
        assert connection.shutdown(1)


def test_gui_reuses_active_logger_updates_plot_and_prevents_protocol_conflict(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController(lambda: RecordingSimulator())
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert window.logging_controllers[1].start(tmp_path, 0.1)
        logger_path = window.logging_controllers[1].snapshot.file_path
        panel = window.voltage_protocol_panels[1]
        panel.duration_input.setText("0.5")
        panel.interval_input.setText("0.1")
        panel.start_button.click()
        protocol = window.voltage_protocol_controllers[1]
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        assert protocol.snapshot.file_path == logger_path
        assert not window.protocol_panels[1].start_button.isEnabled()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        assert window.logging_controllers[1].snapshot.active
        assert window.plot_controllers[1].snapshot.total_samples >= 1
        window.logging_controllers[1].stop("CV integration test complete")
        data = rows(logger_path)  # type: ignore[arg-type]
        assert any(row["protocol_name"] == "constant_voltage" for row in data)
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)


def test_disconnect_and_window_shutdown_attempt_selected_channel_off(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    protocol = ConstantVoltageController(2, connection)
    try:
        assert start(protocol, tmp_path, "2")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.shutdown()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert protocol.snapshot.output_enabled is False
        assert any(call[0] == "output_off" and call[1] == 2 for call in backend.calls)
        assert not any(call[1] == 1 for call in backend.calls)
    finally:
        assert connection.shutdown(1)
