"""Milestone 16 shared validation, event logging and Emergency All-Off."""

from __future__ import annotations

import csv
from collections import deque
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.app.channel_controller import ManualChannelController
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.engine.constant_current import ConstantCurrentController, ProtocolState
from qpx_electrolyser.engine.custom_sequence import SequenceStageType
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.serial_transport import SerialTransport
from qpx_electrolyser.safety.emergency import EmergencyAllOffController
from qpx_electrolyser.safety.event_log import SAFETY_EVENT_COLUMNS, SafetyEventLog
from qpx_electrolyser.safety.model import (
    SafetyLimits,
    SafetyValidationError,
    SafetyValidator,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate, timeout_s: float = 4.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for safety state")


class RecordingSimulator(SimulatedPowerSupply):
    def __init__(self, *, fail_off_channel: int | None = None) -> None:
        super().__init__()
        self.calls: list[tuple[str, int]] = []
        self.fail_off_channel = fail_off_channel

    def output_on(self, channel: int) -> None:
        self.calls.append(("on", channel))
        super().output_on(channel)

    def output_off(self, channel: int) -> None:
        self.calls.append(("off", channel))
        if channel == self.fail_off_channel:
            raise RuntimeError(f"injected CH{channel} Output-Off failure")
        super().output_off(channel)


class FakeSerial:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = deque(responses)
        self.writes: list[bytes] = []
        self.is_open = True

    def open_with(self, **_settings: object) -> "FakeSerial":
        return self

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        del expected, size
        return self.responses.popleft() if self.responses else b""

    def close(self) -> None:
        self.is_open = False


def channel_responses(channel: int, *, output: bool) -> list[bytes]:
    return [
        b"1\r\n",
        f"V{channel} 2.000\r\n".encode(),
        f"I{channel} 0.10\r\n".encode(),
        (b"2.000V\r\n" if output else b"0.000V\r\n"),
        (b"0.04A\r\n" if output else b"0.00A\r\n"),
        (b"1\r\n" if output else b"0\r\n"),
    ]


def connected(qapp: QApplication, backend: RecordingSimulator) -> ConnectionController:
    connection = ConnectionController(lambda: backend)
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    return connection


def set_output(
    qapp: QApplication, connection: ConnectionController, channel: int, enabled: bool
) -> None:
    completed: list[int] = []
    connection.command_completed.connect(lambda request_id, _name, _value: completed.append(request_id))
    ticket = connection.submit_command(
        f"test-ch{channel}-output",
        lambda supply: supply.output_on(channel) if enabled else supply.output_off(channel),
    )
    assert ticket is not None
    wait_until(qapp, lambda: ticket.request_id in completed)


def test_limits_are_independent_and_use_strictest_policy() -> None:
    validator = SafetyValidator({
        1: SafetyLimits(4.0, 0.5, 1.5, 0.2),
        2: SafetyLimits(8.0, 2.0, 10.0, 1.0),
    })
    assert validator.validate_operating_point(1, 3.0, 0.5, "60 V") == (3.0, 0.5)
    assert validator.validate_operating_point(2, 8.0, 1.0, "60 V") == (8.0, 1.0)
    with pytest.raises(SafetyValidationError, match="[Vv]oltage"):
        validator.validate_operating_point(1, 4.001, 0.1, "60 V")
    with pytest.raises(SafetyValidationError, match="power"):
        validator.validate_operating_point(1, 4.0, 0.5, "60 V")
    with pytest.raises(SafetyValidationError, match="current density"):
        validator.validate_operating_point(
            1, 1.0, 0.3, "60 V", active_area_cm2=1.0
        )


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan")])
def test_limits_reject_nonpositive_or_nonfinite_values(bad: float) -> None:
    with pytest.raises(SafetyValidationError):
        SafetyLimits(bad, 1.0, 1.0, 1.0)


def test_grid_validation_and_safe_zero_baseline() -> None:
    validator = SafetyValidator()
    assert validator.validate_operating_point(1, 0.0, 0.0, "60 V") == (0.0, 0.0)
    with pytest.raises(SafetyValidationError, match="increments"):
        validator.validate_operating_point(1, 1.0005, 0.1, "60 V")
    with pytest.raises(SafetyValidationError, match="increments"):
        validator.validate_operating_point(1, 1.0, 0.015, "60 V")


def test_failed_manual_validation_submits_no_mutation(
    qapp: QApplication,
) -> None:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    validator = SafetyValidator({1: SafetyLimits(1.0, 1.0, 1.0, 1.0)})
    manual = ManualChannelController(1, connection, safety_validator=validator)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: manual.snapshot.connected)
        manual.request_set_voltage("2")
        qapp.processEvents()
        assert manual.snapshot.error
        assert "safety limit" in manual.snapshot.message
        assert backend.calls == []
    finally:
        assert connection.shutdown(1)


def test_failed_protocol_validation_precedes_ownership_logging_and_mutation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = connected(qapp, backend)
    validator = SafetyValidator({1: SafetyLimits(1.0, 1.0, 1.0, 1.0)})
    protocol = ConstantCurrentController(1, connection, safety_validator=validator)
    try:
        assert not protocol.start("0.1", "2", "2", "0.1", "60 V", tmp_path)
        assert protocol.snapshot.state is ProtocolState.FAILED
        assert protocol.snapshot.file_path is None
        assert "safety limit" in protocol.snapshot.message
        assert backend.calls == []
        assert not list(tmp_path.glob("*.csv"))
    finally:
        protocol.shutdown()
        assert connection.shutdown(1)


def test_emergency_all_off_is_independent_exactly_once_and_logged(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = connected(qapp, backend)
    set_output(qapp, connection, 1, True)
    set_output(qapp, connection, 2, True)
    backend.calls.clear()
    event_log = SafetyEventLog(tmp_path / "safety.csv")
    emergency = EmergencyAllOffController(connection, event_log)
    emergency.register({}, {})
    try:
        assert emergency.request()
        assert not emergency.request()
        wait_until(qapp, lambda: not emergency.snapshot.active and emergency.snapshot.results)
        assert not emergency.snapshot.warning
        assert backend.calls.count(("off", 1)) == 1
        assert backend.calls.count(("off", 2)) == 1
        assert all(item.confirmed_off for item in emergency.snapshot.results)
        with event_log.path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        assert tuple(rows[0]) == SAFETY_EVENT_COLUMNS
        assert [row["channel"] for row in rows] == ["1", "2"]
        assert {row["final_state"] for row in rows} == {"OFF_CONFIRMED"}
        assert not any("QPX" in row["cause"] for row in rows)
    finally:
        assert connection.shutdown(1)


def test_emergency_partial_failure_reports_channels_separately_and_disconnects(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator(fail_off_channel=1)
    connection = connected(qapp, backend)
    set_output(qapp, connection, 1, True)
    set_output(qapp, connection, 2, True)
    backend.calls.clear()
    emergency = EmergencyAllOffController(connection, SafetyEventLog(tmp_path / "events.csv"))
    emergency.register({}, {})
    assert emergency.request()
    wait_until(qapp, lambda: not emergency.snapshot.active and emergency.snapshot.results)
    assert emergency.snapshot.warning
    results = {item.channel: item for item in emergency.snapshot.results}
    assert not results[1].confirmed_off
    assert results[2].confirmed_off
    assert backend.calls.count(("off", 1)) == 1
    assert backend.calls.count(("off", 2)) == 1
    wait_until(qapp, lambda: connection.snapshot.state is not ConnectionState.CONNECTED)
    assert connection.shutdown(1)


def test_emergency_stops_active_protocol_without_second_off_write(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    connection = connected(qapp, backend)
    protocol = ConstantCurrentController(1, connection)
    emergency = EmergencyAllOffController(connection, SafetyEventLog(tmp_path / "events.csv"))
    emergency.register({1: (protocol,)}, {})
    try:
        assert protocol.start("0.1", "2", "5", "0.1", "60 V", tmp_path)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        backend.calls.clear()
        assert emergency.request()
        wait_until(qapp, lambda: not emergency.snapshot.active and emergency.snapshot.results)
        assert protocol.snapshot.state is ProtocolState.COMPLETED
        assert backend.calls.count(("off", 1)) == 1
        assert backend.calls.count(("off", 2)) == 0
        assert all(item.confirmed_off for item in emergency.snapshot.results)
    finally:
        protocol.shutdown()
        assert connection.shutdown(1)


def test_idle_disconnected_emergency_never_claims_success(tmp_path: Path) -> None:
    connection = ConnectionController()
    emergency = EmergencyAllOffController(connection, SafetyEventLog(tmp_path / "events.csv"))
    emergency.register({}, {})
    assert emergency.request()
    # The zero-delay Qt callback is intentionally bypassed here: disconnected
    # state can never authoritatively prove physical OFF.
    emergency._await_protocol_cleanup()
    assert not emergency.snapshot.active
    assert emergency.snapshot.warning
    assert all(not item.confirmed_off for item in emergency.snapshot.results)
    assert connection.shutdown(1)


def test_safety_event_log_redacts_identity_serial(tmp_path: Path) -> None:
    event_log = SafetyEventLog(tmp_path / "events.csv")
    event_log.append(
        channel=1,
        cause="Aim-TTi,QPX600DP,123456,1.00 communication failure",
        requested_action="Output Off",
        confirmed_off=False,
    )
    content = event_log.path.read_text(encoding="utf-8")
    assert "123456" not in content
    assert "[REDACTED]" in content


def test_real_fake_serial_emergency_uses_guarded_exactly_once_sequence(
    qapp: QApplication, tmp_path: Path,
) -> None:
    responses = [
        b"Aim-TTi,QPX600DP,SECRET,1.00\r\n",
        *channel_responses(1, output=True),
        *channel_responses(2, output=True),
        b"1\r\n",  # initial OP1?
        b"1\r\n",  # *OPC?
        b"0\r\n", b"0\r\n",  # stable OP1? confirmations
        *channel_responses(1, output=False),
        b"1\r\n",  # initial OP2?
        b"1\r\n",  # *OPC?
        b"0\r\n", b"0\r\n",  # stable OP2? confirmations
        *channel_responses(2, output=False),
    ]
    fake = FakeSerial(responses)
    connection = ConnectionController(
        real_backend_factory=lambda port: QPX600DPDriver(
            SerialTransport(port, serial_factory=fake.open_with)
        )
    )
    connection.set_simulation_mode(False)
    connection.set_port_name("COM99")
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    emergency = EmergencyAllOffController(
        connection, SafetyEventLog(tmp_path / "events.csv"), sleep=lambda _seconds: None
    )
    emergency.register({}, {})
    assert emergency.request()
    wait_until(qapp, lambda: not emergency.snapshot.active and emergency.snapshot.results)
    assert not emergency.snapshot.warning
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.writes.count(b"OP2 0\n") == 1
    assert fake.writes.count(b"*OPC?\n") == 2
    emergency_commands = fake.writes[13:]
    assert emergency_commands == [
        b"OP1?\n", b"OP1 0\n", b"*OPC?\n", b"OP1?\n", b"OP1?\n",
        b"VRANGE1?\n", b"V1?\n", b"I1?\n", b"V1O?\n", b"I1O?\n", b"OP1?\n",
        b"OP2?\n", b"OP2 0\n", b"*OPC?\n", b"OP2?\n", b"OP2?\n",
        b"VRANGE2?\n", b"V2?\n", b"I2?\n", b"V2O?\n", b"I2O?\n", b"OP2?\n",
    ]
    assert connection.shutdown(1)


def connected_window(
    qapp: QApplication, tmp_path: Path,
) -> tuple[ConnectionController, MainWindow]:
    connection = ConnectionController(SimulatedPowerSupply)
    window = MainWindow(
        tmp_path / "application.log", connection, data_directory=tmp_path / "data"
    )
    connection.request_connect()
    wait_until(
        qapp,
        lambda: connection.snapshot.state is ConnectionState.CONNECTED
        and all(item.snapshot.connected for item in window.channel_controllers.values()),
    )
    return connection, window


def test_live_safety_feedback_is_immediate_exact_and_channel_local(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, window = connected_window(qapp, tmp_path)
    try:
        cv1 = window.voltage_protocol_panels[1]
        cv2 = window.voltage_protocol_panels[2]
        assert cv1.start_button.isEnabled()
        assert cv2.start_button.isEnabled()

        window.safety_panel.limits_changed.emit(
            1, SafetyLimits(1.0, 5.0, 50.0, 5.0)
        )
        qapp.processEvents()
        expected = "Target voltage 2 V exceeds CH1 safety limit 1 V."
        assert cv1.message_label.text() == expected
        assert cv1.start_button.toolTip() == expected
        assert not cv1.start_button.isEnabled()
        assert cv2.start_button.isEnabled()
        assert "CH1 safety limit" not in cv2.message_label.text()

        cv1.target_voltage_input.setText("1")
        qapp.processEvents()
        assert cv1.start_button.isEnabled()
        assert cv1.start_button.toolTip() == ""
        assert "exceeds" not in cv1.message_label.text()
    finally:
        window.close()
        assert connection.shutdown(1)


def test_live_safety_feedback_covers_m9_through_m15_panels(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, window = connected_window(qapp, tmp_path)
    try:
        sequence = window.sequence_panels[1]
        sequence.stage_type.setCurrentIndex(
            sequence.stage_type.findData(SequenceStageType.CONSTANT_VOLTAGE.value)
        )
        sequence.add_button.click()
        qapp.processEvents()
        window.safety_panel.limits_changed.emit(
            1, SafetyLimits(1.0, 5.0, 50.0, 5.0)
        )
        qapp.processEvents()

        panels = (
            window.protocol_panels[1],
            window.voltage_protocol_panels[1],
            window.voltage_scan_panels[1],
            window.current_scan_panels[1],
            window.polarisation_panels[1],
            window.sequence_panels[1],
        )
        for panel in panels:
            assert not panel.start_button.isEnabled(), type(panel).__name__
            assert "exceeds CH1 safety limit 1 V" in panel.message_label.text()
            assert panel.start_button.toolTip() == panel.message_label.text()

        # No CH1 policy feedback leaks into the independent CH2 protocol panels.
        for panel in (
            window.protocol_panels[2],
            window.voltage_protocol_panels[2],
            window.voltage_scan_panels[2],
            window.current_scan_panels[2],
            window.polarisation_panels[2],
        ):
            assert panel.start_button.isEnabled(), type(panel).__name__
            assert "CH1 safety limit" not in panel.message_label.text()
    finally:
        window.close()
        assert connection.shutdown(1)


def test_typing_does_not_log_but_explicit_unsafe_start_attempt_does(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, window = connected_window(qapp, tmp_path)
    event_path = tmp_path / "safety_events.csv"
    try:
        window.safety_panel.limits_changed.emit(
            1, SafetyLimits(1.0, 5.0, 50.0, 5.0)
        )
        panel = window.voltage_protocol_panels[1]
        panel.target_voltage_input.setText("3")
        qapp.processEvents()
        assert not event_path.exists()

        window._start_voltage_protocol(1, "3", "0.1", "2", "1")
        assert event_path.exists()
        with event_path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 1
        assert rows[0]["channel"] == "1"
        assert rows[0]["cause"] == "Target voltage 3 V exceeds CH1 safety limit 1 V."
        assert rows[0]["requested_action"] == (
            "Blocked unsafe Constant Voltage Start before mutation"
        )
        assert rows[0]["final_state"] == "OFF_CONFIRMED"

        panel.target_voltage_input.setText("4")
        panel.target_voltage_input.setText("3")
        qapp.processEvents()
        with event_path.open(newline="", encoding="utf-8") as stream:
            assert len(list(csv.DictReader(stream))) == 1
    finally:
        window.close()
        assert connection.shutdown(1)
