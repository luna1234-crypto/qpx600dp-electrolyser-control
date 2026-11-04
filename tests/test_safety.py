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
    with pytest.raises(SafetyValidationError, match="voltage"):
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
