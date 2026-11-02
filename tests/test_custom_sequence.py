"""Milestone 15 orchestration tests; simulator only unless a fake is explicit."""

from collections.abc import Callable
import csv
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.engine.custom_sequence import (
    CustomSequenceController,
    REAL_HARDWARE_SEQUENCE_CONFIRMATION,
    SequenceLogSink,
    SequenceStage,
    SequenceStageType,
)
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.serial_transport import SerialTransport
from qpx_electrolyser.gui.custom_sequence_panel import CustomSequencePanel
from qpx_electrolyser.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 10) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("Timed out waiting for Custom Sequence state")


class RecordingSimulator(SimulatedPowerSupply):
    def __init__(self) -> None:
        super().__init__(SimulatorConfig(communication_delay_s=0.001))
        self.calls: list[tuple[str, int, float | None]] = []

    def set_voltage(self, channel: int, value: float) -> None:
        self.calls.append(("set_voltage", channel, value)); super().set_voltage(channel, value)

    def set_current(self, channel: int, value: float) -> None:
        self.calls.append(("set_current", channel, value)); super().set_current(channel, value)

    def output_on(self, channel: int) -> None:
        self.calls.append(("output_on", channel, None)); super().output_on(channel)

    def output_off(self, channel: int) -> None:
        self.calls.append(("output_off", channel, None)); super().output_off(channel)


class FakeSerial:
    """Command-aware fake used only to prove delegated guarded serial behavior."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.voltage = {1: 0.0, 2: 0.0}
        self.current = {1: 0.1, 2: 0.1}
        self.output = {1: False, 2: False}
        self.identification = b"THURLBY THANDAR,QPX600DP,REDACTED,1.00\r\n"
        self._query = ""
        self.is_open = True

    def open_with(self, **_settings):  # type: ignore[no-untyped-def]
        self.is_open = True
        return self

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        command = data.decode("ascii").strip()
        self._query = command if command.endswith("?") else ""
        if command.startswith("V") and not command.endswith("?"):
            channel, value = command[1:].split(); self.voltage[int(channel)] = float(value)
        elif command.startswith("I") and not command.endswith("?"):
            channel, value = command[1:].split(); self.current[int(channel)] = float(value)
        elif command.startswith("OP") and not command.endswith("?"):
            channel, value = command[2:].split(); self.output[int(channel)] = value == "1"
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        command = self._query
        if command == "*IDN?": return self.identification
        if command == "*OPC?": return b"1\r\n"
        if command.startswith("VRANGE"): return b"1\r\n"
        if command.startswith("OP"):
            channel = int(command[2]); return (b"1\r\n" if self.output[channel] else b"0\r\n")
        if command.startswith("V") and command.endswith("O?"):
            channel = int(command[1]); value = self.voltage[channel] if self.output[channel] else 0
            return f"{value:.3f}V\r\n".encode()
        if command.startswith("I") and command.endswith("O?"):
            channel = int(command[1]); value = min(self.current[channel], self.voltage[channel] / 100) if self.output[channel] else 0
            return f"{value:.3f}A\r\n".encode()
        if command.startswith("V"):
            channel = int(command[1]); return f"V{channel} {self.voltage[channel]:.3f}\r\n".encode()
        if command.startswith("I"):
            channel = int(command[1]); return f"I{channel} {self.current[channel]:.3f}\r\n".encode()
        return b""

    def close(self) -> None:
        self.is_open = False


def connected(qapp: QApplication) -> tuple[ConnectionController, RecordingSimulator]:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    backend.calls.clear()
    return connection, backend


def cc(duration: str = "0.3") -> SequenceStage:
    return SequenceStage.create(
        SequenceStageType.CONSTANT_CURRENT,
        current="0.10", voltage="2", duration=duration, interval="0.1",
        current_input_mode="current", active_area_cm2="1",
    )


def cv(duration: str = "0.3") -> SequenceStage:
    return SequenceStage.create(
        SequenceStageType.CONSTANT_VOLTAGE,
        voltage="1.8", current="0.20", duration=duration, interval="0.1",
    )


def wait(duration: str = "0.1") -> SequenceStage:
    return SequenceStage.create(SequenceStageType.WAIT_OUTPUT_OFF, duration=duration)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_complete_prevalidation_happens_before_file_or_mutation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    controller = CustomSequenceController(1, connection)
    try:
        bad = SequenceStage.create(
            SequenceStageType.CONSTANT_VOLTAGE,
            voltage="not-a-number", current="0.2", duration="1", interval="0.1",
        )
        assert not controller.start("invalid", (cc(), bad), "60 V", tmp_path)
        assert "Stage 2" in controller.snapshot.message
        assert backend.calls == []
        assert not list(tmp_path.glob("*.csv"))
    finally:
        controller.shutdown(); assert connection.shutdown(2)


def test_order_exactly_once_metadata_wait_and_single_writer(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    controller = CustomSequenceController(1, connection)
    try:
        assert controller.start("ordered", (cc(), wait(), cv()), "60 V", tmp_path)
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.COMPLETED)
        path = controller.snapshot.file_path
        assert path is not None and path.is_file()
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
        rows = read_rows(path)
        completed = [row["sequence_stage_type"] for row in rows
                     if row["sample_status"] == "stage_completed"]
        assert completed == ["constant_current", "wait_output_off", "constant_voltage"]
        assert {row["sequence_name"] for row in rows} == {"ordered"}
        assert len({row["sequence_run_id"] for row in rows}) == 1
        assert [call[0] for call in backend.calls].count("output_on") == 2
        assert [call[0] for call in backend.calls].count("output_off") == 2
        assert backend.read_output_state(1) is False
        assert all(call[1] == 1 for call in backend.calls)
    finally:
        controller.shutdown(); assert connection.shutdown(2)


def test_pause_resume_same_stage_and_early_stop_no_advancement(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    controller = CustomSequenceController(1, connection)
    try:
        assert controller.start("pause", (cc("0.8"), cv()), "60 V", tmp_path)
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.RUNNING)
        time.sleep(0.1); qapp.processEvents(); controller.pause()
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.PAUSED)
        stage = controller.snapshot.stage_index
        elapsed = controller.snapshot.elapsed_active_s
        time.sleep(0.12); qapp.processEvents()
        assert controller.snapshot.stage_index == stage
        assert controller.snapshot.elapsed_active_s == pytest.approx(elapsed, abs=0.03)
        assert controller.resume()
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.RUNNING)
        controller.stop()
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.COMPLETED)
        rows = read_rows(controller.snapshot.file_path)  # type: ignore[arg-type]
        assert not any(
            row["sequence_stage_type"] == "constant_voltage"
            for row in rows
        )
        assert backend.read_output_state(1) is False
    finally:
        controller.shutdown(); assert connection.shutdown(2)


def test_simultaneous_channels_pause_isolation_and_acceptance_evidence(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    ch1 = CustomSequenceController(1, connection)
    ch2 = CustomSequenceController(2, connection)
    stages1 = (cc("2"), wait("1"), cv("2"))
    stages2 = (cv("2"), cc("2"))
    try:
        assert ch1.start("CH1 acceptance", stages1, "60 V", tmp_path)
        assert ch2.start("CH2 acceptance", stages2, "60 V", tmp_path)
        wait_until(qapp, lambda: ch1.snapshot.state is ProtocolState.RUNNING
                   and ch2.snapshot.state is ProtocolState.RUNNING)
        ch1.pause()
        wait_until(qapp, lambda: ch1.snapshot.state is ProtocolState.PAUSED)
        assert ch2.snapshot.state is ProtocolState.RUNNING
        assert backend.read_output_state(1) is False
        assert ch1.resume()
        wait_until(qapp, lambda: ch1.snapshot.state is ProtocolState.RUNNING)
        wait_until(qapp, lambda: ch1.snapshot.state is ProtocolState.COMPLETED
                   and ch2.snapshot.state is ProtocolState.COMPLETED)
        for controller, expected in (
            (ch1, ["constant_current", "wait_output_off", "constant_voltage"]),
            (ch2, ["constant_voltage", "constant_current"]),
        ):
            rows = read_rows(controller.snapshot.file_path)  # type: ignore[arg-type]
            observed = [row["sequence_stage_type"] for row in rows
                        if row["sample_status"] == "stage_completed"]
            assert observed == expected
            assert len(observed) == len(set(enumerate(observed)))
        assert backend.read_output_state(1) is False
        assert backend.read_output_state(2) is False
    finally:
        ch1.shutdown(); ch2.shutdown(); assert connection.shutdown(2)


def test_editor_add_duplicate_reorder_remove_and_gui_start(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show()
    try:
        connection.request_connect()
        wait_until(
            qapp,
            lambda: connection.snapshot.state is ConnectionState.CONNECTED
            and all(item.snapshot.voltage_range_name for item in window.channel_controllers.values()),
        )
        backend.calls.clear()
        panel: CustomSequencePanel = window.sequence_panels[1]
        panel.stage_type.setCurrentIndex(
            panel.stage_type.findData(SequenceStageType.CONSTANT_VOLTAGE.value)
        )
        panel.field_inputs[2].setText("0.3")
        panel.field_inputs[3].setText("0.1")
        panel.add_button.click()
        panel.duplicate_button.click()
        assert len(panel.stages) == 2
        panel.up_button.click()
        panel.remove_button.click()
        assert len(panel.stages) == 1
        assert panel.start_button.isEnabled()
        panel.start_button.click()
        wait_until(
            qapp,
            lambda: window.sequence_controllers[1].snapshot.state
            is ProtocolState.COMPLETED,
        )
        assert all(call[1] == 1 for call in backend.calls)
        assert backend.read_output_state(1) is False
    finally:
        window.close(); assert connection.shutdown(2)


def test_double_start_disconnect_and_stale_terminal_are_bounded(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    controller = CustomSequenceController(1, connection)
    try:
        assert controller.start("bounded", (cc("2"), cv()), "60 V", tmp_path)
        assert not controller.start("duplicate", (cv(),), "60 V", tmp_path)
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.RUNNING)
        active = controller._active_controller
        controller.stop("Protocol stopped before disconnect")
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.COMPLETED)
        assert backend.read_output_state(1) is False
        terminal = controller.snapshot
        if active is not None:
            controller._child_snapshot(active, active.snapshot)
        assert controller.snapshot == terminal
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
        connection.request_disconnect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.DISCONNECTED)
    finally:
        controller.shutdown(); assert connection.shutdown(2)


def test_sequence_writer_failure_triggers_existing_safe_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingWriter:
        def __init__(self, directory: Path, channel: int) -> None:
            self.path = Path(directory) / f"failure_ch{channel}.csv"
            self.path.touch(exist_ok=False)
            self.closed = False

        def append(self, *, readback=None, **_metadata) -> None:  # type: ignore[no-untyped-def]
            if readback is not None:
                raise OSError("simulated sequence disk failure")

        def close(self) -> None:
            self.closed = True

    def sink_factory(directory, channel, name, run_id, session_id, **kwargs):  # type: ignore[no-untyped-def]
        return SequenceLogSink(
            directory, channel, name, run_id, session_id,
            monotonic=kwargs["monotonic"], writer_factory=FailingWriter,
        )

    connection, backend = connected(qapp)
    controller = CustomSequenceController(1, connection, sink_factory=sink_factory)
    try:
        assert controller.start("failure", (cc("1"),), "60 V", tmp_path)
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.FAILED)
        assert "CSV" in controller.snapshot.message
        assert backend.read_output_state(1) is False
        assert [call[0] for call in backend.calls].count("output_on") == 1
        assert [call[0] for call in backend.calls].count("output_off") == 1
    finally:
        controller.shutdown(); assert connection.shutdown(2)


@pytest.mark.parametrize("channel", [1, 2])
def test_fake_serial_sequence_reuses_exact_guarded_transition_order(
    qapp: QApplication, tmp_path: Path, channel: int,
) -> None:
    fake = FakeSerial()

    def factory(port: str) -> QPX600DPDriver:
        assert port == "COM11"
        return QPX600DPDriver(SerialTransport(port, serial_factory=fake.open_with))

    connection = ConnectionController(real_backend_factory=factory)
    connection.set_simulation_mode(False)
    connection.set_port_name("COM11")
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    fake.writes.clear()
    controller = CustomSequenceController(channel, connection)
    try:
        stage = SequenceStage.create(
            SequenceStageType.CONSTANT_CURRENT,
            current="0.10", voltage="2", duration="2", interval="1",
            current_input_mode="current", active_area_cm2="1",
        )
        assert controller.start(
            "fake guarded", (stage,), "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_SEQUENCE_CONFIRMATION,
        )
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.RUNNING, 6)
        controller.stop()
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.COMPLETED, 6)
        expected_preflight = [
            b"*IDN?\n",
            b"VRANGE1?\n", b"V1?\n", b"I1?\n", b"V1O?\n", b"I1O?\n", b"OP1?\n",
            b"VRANGE2?\n", b"V2?\n", b"I2?\n", b"V2O?\n", b"I2O?\n", b"OP2?\n",
        ]
        assert fake.writes[:13] == expected_preflight
        on = f"OP{channel} 1\n".encode()
        off = f"OP{channel} 0\n".encode()
        voltage = f"V{channel} 2.000\n".encode()
        current = f"I{channel} 0.10\n".encode()
        assert fake.writes.count(on) == 1
        assert fake.writes.count(off) == 1
        assert fake.writes.count(b"*OPC?\n") == 2
        assert fake.writes.index(voltage) < fake.writes.index(on)
        assert fake.writes.index(current) < fake.writes.index(on)
        assert fake.output == {1: False, 2: False}
        other = 2 if channel == 1 else 1
        assert not any(
            item.startswith((f"V{other} ".encode(), f"I{other} ".encode(), f"OP{other} ".encode()))
            for item in fake.writes
        )
    finally:
        controller.shutdown(); assert connection.shutdown(2)


@pytest.mark.parametrize("channel", [1, 2])
def test_fresh_preflight_rejects_selected_output_on_without_mutation(
    qapp: QApplication, tmp_path: Path, channel: int,
) -> None:
    fake = FakeSerial()

    def factory(port: str) -> QPX600DPDriver:
        assert port == "COM11"
        return QPX600DPDriver(SerialTransport(port, serial_factory=fake.open_with))

    connection = ConnectionController(real_backend_factory=factory)
    connection.set_simulation_mode(False); connection.set_port_name("COM11")
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    fake.writes.clear()
    fake.output[channel] = True  # Changes after the GUI's initial connection snapshot.
    controller = CustomSequenceController(channel, connection)
    try:
        stage = SequenceStage.create(
            SequenceStageType.CONSTANT_CURRENT,
            current="0.10", voltage="2", duration="2", interval="1",
            current_input_mode="current", active_area_cm2="1",
        )
        assert controller.start(
            "fresh ON rejection", (stage,), "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_SEQUENCE_CONFIRMATION,
        )
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.FAILED)
        assert "output is ON" in controller.snapshot.message
        assert not any(
            item.startswith((b"V1 ", b"I1 ", b"OP1 ", b"V2 ", b"I2 ", b"OP2 "))
            for item in fake.writes
        )
        assert not list(tmp_path.glob("*.csv"))
    finally:
        fake.output[channel] = False
        controller.shutdown(); assert connection.shutdown(2)


def test_real_pause_resume_continues_same_stage_with_exact_transitions(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial()

    def factory(port: str) -> QPX600DPDriver:
        return QPX600DPDriver(SerialTransport(port, serial_factory=fake.open_with))

    connection = ConnectionController(real_backend_factory=factory)
    connection.set_simulation_mode(False); connection.set_port_name("COM11")
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    fake.writes.clear()
    controller = CustomSequenceController(1, connection)
    try:
        first = SequenceStage.create(
            SequenceStageType.CONSTANT_CURRENT,
            current="0.10", voltage="2", duration="10", interval="1",
            current_input_mode="current", active_area_cm2="1",
        )
        second = SequenceStage.create(
            SequenceStageType.CONSTANT_VOLTAGE,
            voltage="1.8", current="0.20", duration="2", interval="1",
        )
        assert controller.start(
            "pause resume", (first, second), "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_SEQUENCE_CONFIRMATION,
        )
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.RUNNING, 8)
        controller.pause()
        wait_until(
            qapp,
            lambda: controller.snapshot.state is ProtocolState.PAUSED
            and controller.snapshot.resume_ready,
            8,
        )
        assert controller.snapshot.stage_index == 1
        assert controller.resume(REAL_HARDWARE_SEQUENCE_CONFIRMATION)
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.RUNNING, 8)
        assert controller.snapshot.stage_index == 1
        controller.stop()
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.COMPLETED, 8)
        assert fake.writes.count(b"OP1 1\n") == 2
        assert fake.writes.count(b"OP1 0\n") == 2
        assert fake.output == {1: False, 2: False}
        rows = read_rows(controller.snapshot.file_path)  # type: ignore[arg-type]
        assert not any(row["sequence_stage_type"] == "constant_voltage" for row in rows)
    finally:
        controller.shutdown(); assert connection.shutdown(2)


def test_fresh_identity_change_is_rejected_before_file_or_mutation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial()

    def factory(port: str) -> QPX600DPDriver:
        return QPX600DPDriver(SerialTransport(port, serial_factory=fake.open_with))

    connection = ConnectionController(real_backend_factory=factory)
    connection.set_simulation_mode(False); connection.set_port_name("COM11")
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    fake.writes.clear()
    fake.identification = b"OTHER,NOT-QPX,REDACTED,1.00\r\n"
    controller = CustomSequenceController(1, connection)
    stage = SequenceStage.create(
        SequenceStageType.CONSTANT_CURRENT,
        current="0.10", voltage="2", duration="2", interval="1",
        current_input_mode="current", active_area_cm2="1",
    )
    try:
        assert controller.start(
            "identity", (stage,), "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_SEQUENCE_CONFIRMATION,
        )
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.FAILED)
        assert "not QPX600DP" in controller.snapshot.message
        assert not any(
            item.startswith((b"V1 ", b"I1 ", b"OP1 ")) for item in fake.writes
        )
        assert not list(tmp_path.glob("*.csv"))
    finally:
        controller.shutdown(); assert connection.shutdown(2)


def test_real_confirmation_cancel_submits_no_preflight_or_file(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSerial()

    def factory(port: str) -> QPX600DPDriver:
        return QPX600DPDriver(SerialTransport(port, serial_factory=fake.open_with))

    connection = ConnectionController(real_backend_factory=factory)
    connection.set_simulation_mode(False); connection.set_port_name("COM11")
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show(); connection.request_connect()
    wait_until(
        qapp,
        lambda: connection.snapshot.state is ConnectionState.CONNECTED
        and all(item.snapshot.voltage_range_name for item in window.channel_controllers.values()),
    )
    try:
        panel = window.sequence_panels[1]
        panel.stage_type.setCurrentIndex(
            panel.stage_type.findData(SequenceStageType.CONSTANT_CURRENT.value)
        )
        panel.field_inputs[2].setText("2")
        panel.field_inputs[3].setText("1")
        panel.add_button.click()
        assert panel.start_button.isEnabled()
        fake.writes.clear()
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Cancel
        )
        panel.start_button.click(); qapp.processEvents()
        assert fake.writes == []
        assert window.sequence_controllers[1].snapshot.state is ProtocolState.IDLE
        assert not list(tmp_path.glob("qpx600dp_ch1_*.csv"))
    finally:
        window.close(); assert connection.shutdown(2)


def test_real_two_stage_transition_advances_once_and_finishes_off(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial()

    def factory(port: str) -> QPX600DPDriver:
        return QPX600DPDriver(SerialTransport(port, serial_factory=fake.open_with))

    connection = ConnectionController(real_backend_factory=factory)
    connection.set_simulation_mode(False); connection.set_port_name("COM11")
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    fake.writes.clear()
    controller = CustomSequenceController(1, connection)
    first = SequenceStage.create(
        SequenceStageType.CONSTANT_CURRENT,
        current="0.10", voltage="2", duration="1", interval="1",
        current_input_mode="current", active_area_cm2="1",
    )
    second = SequenceStage.create(
        SequenceStageType.CONSTANT_VOLTAGE,
        voltage="1.8", current="0.20", duration="1", interval="1",
    )
    try:
        assert controller.start(
            "two stages", (first, second), "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_SEQUENCE_CONFIRMATION,
        )
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.COMPLETED, 15)
        rows = read_rows(controller.snapshot.file_path)  # type: ignore[arg-type]
        completed = [
            row["sequence_stage_type"] for row in rows
            if row["sample_status"] == "stage_completed"
        ]
        assert completed == ["constant_current", "constant_voltage"]
        assert fake.writes.count(b"OP1 1\n") == 2
        assert fake.writes.count(b"OP1 0\n") == 2
        assert fake.output == {1: False, 2: False}
    finally:
        controller.shutdown(); assert connection.shutdown(2)
