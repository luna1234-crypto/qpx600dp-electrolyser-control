"""Safety gates for the explicit-port hardware check; all tests use a fake driver."""

from argparse import Namespace
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from qpx_electrolyser.instrument.hardware_check import (
    HardwareCheckAuthorizationError,
    MAX_MONITORED_DURATION_S,
    MONITORED_OUTPUT_CONFIRMATION,
    MonitoredOutputSafetyError,
    OUTPUT_ON_CONFIRMATION,
    STATUS_FORENSICS_CONFIRMATION,
    StatusForensicsSafetyError,
    WRITE_CONFIRMATION,
    build_parser,
    run_check,
    validate_authorization,
)
from qpx_electrolyser.instrument.exceptions import (
    InstrumentProtocolError,
    InstrumentTimeoutError,
)
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.serial_transport import SerialTransport


class RecordingDriver:
    def __init__(self) -> None:
        self.operations: list[tuple[object, ...]] = []

    def connect(self) -> None:
        self.operations.append(("connect",))

    def disconnect(self) -> None:
        self.operations.append(("disconnect",))

    def identify(self) -> str:
        self.operations.append(("identify",))
        return "THURLBY THANDAR,QPX600DP,123,1.00"

    def set_voltage(self, channel: int, value: float) -> None:
        self.operations.append(("set_voltage", channel, value))

    def set_current(self, channel: int, value: float) -> None:
        self.operations.append(("set_current", channel, value))

    def output_on(self, channel: int) -> None:
        self.operations.append(("output_on", channel))

    def output_off(self, channel: int) -> None:
        self.operations.append(("output_off", channel))

    def read_voltage_range(self, channel: int) -> SimpleNamespace:
        self.operations.append(("read_voltage_range", channel))
        return SimpleNamespace(name="60 V")

    def read_set_voltage(self, channel: int) -> float:
        self.operations.append(("read_set_voltage", channel))
        return 1.0

    def read_set_current(self, channel: int) -> float:
        self.operations.append(("read_set_current", channel))
        return 0.1

    def read_output_state(self, channel: int) -> bool:
        self.operations.append(("read_output_state", channel))
        return False

    def read_voltage(self, channel: int) -> float:
        self.operations.append(("read_voltage", channel))
        return 0.0

    def read_current(self, channel: int) -> float:
        self.operations.append(("read_current", channel))
        return 0.0

    def read_status_byte(self) -> int:
        self.operations.append(("read_status_byte",))
        return 0


def parse(*arguments: str) -> Namespace:
    return build_parser().parse_args(["--port", "COM8", *arguments])


def test_default_hardware_check_is_read_only_and_checks_both_channels() -> None:
    args = parse()
    driver = RecordingDriver()
    output: list[str] = []
    run_check(args, driver_factory=lambda _port, _timeout: driver, emit=output.append)
    operation_names = [operation[0] for operation in driver.operations]
    assert operation_names[0:2] == ["connect", "identify"]
    assert operation_names[-2:] == ["read_status_byte", "disconnect"]
    assert "set_voltage" not in operation_names
    assert "set_current" not in operation_names
    assert "output_on" not in operation_names
    assert "output_off" not in operation_names
    assert ("read_voltage_range", 1) in driver.operations
    assert ("read_voltage_range", 2) in driver.operations
    assert output[0].startswith("Identification: THURLBY THANDAR,QPX600DP")


def test_status_forensics_confirmation_is_rejected_before_driver_creation() -> None:
    created = False

    def factory(_port: str, _timeout: float) -> RecordingDriver:
        nonlocal created
        created = True
        return RecordingDriver()

    with pytest.raises(HardwareCheckAuthorizationError, match="exact token"):
        run_check(
            parse("--capture-status-once", "wrong"),
            driver_factory=factory,
        )
    assert created is False


@pytest.mark.parametrize("conflict", [
    ("--channel", "1"),
    ("--confirm-write", WRITE_CONFIRMATION),
    ("--set-voltage", "1"),
    ("--output-off",),
])
def test_status_forensics_rejects_conflicting_options_before_connect(
    conflict: tuple[str, ...],
) -> None:
    with pytest.raises(HardwareCheckAuthorizationError, match="cannot be combined"):
        validate_authorization(parse(
            "--capture-status-once", STATUS_FORENSICS_CONFIRMATION, *conflict,
        ))


@pytest.mark.parametrize("arguments,match", [
    (("--channel", "1", "--set-voltage", "1"), "confirm-write"),
    (("--confirm-write", WRITE_CONFIRMATION, "--set-voltage", "1"), "channel"),
    (("--channel", "1", "--output-off"), "confirm-write"),
    (("--channel", "1", "--output-on-for", "1", "--confirm-write", WRITE_CONFIRMATION),
     "confirm-output-on"),
])
def test_writes_require_explicit_authorization(
    arguments: tuple[str, ...], match: str,
) -> None:
    with pytest.raises(HardwareCheckAuthorizationError, match=match):
        validate_authorization(parse(*arguments))


@pytest.mark.parametrize("arguments,match", [
    (("--output-on-for", "0"), "positive"),
    (("--output-on-for", "1"), "set-voltage"),
    (("--output-on-for", "1", "--set-voltage", "2", "--set-current", "1"),
     "safety-max"),
    (("--output-on-for", "1", "--set-voltage", "2", "--set-current", "1",
      "--safety-max-voltage", "1", "--safety-max-current", "2",
      "--safety-max-power", "2"), "Voltage"),
    (("--output-on-for", "1", "--set-voltage", "2", "--set-current", "3",
      "--safety-max-voltage", "2", "--safety-max-current", "3",
      "--safety-max-power", "5"), "power"),
])
def test_output_on_requires_setpoints_and_explicit_safety_limits(
    arguments: tuple[str, ...], match: str,
) -> None:
    base = (
        "--channel", "1",
        "--confirm-write", WRITE_CONFIRMATION,
        "--confirm-output-on", OUTPUT_ON_CONFIRMATION,
    )
    with pytest.raises(HardwareCheckAuthorizationError, match=match):
        validate_authorization(parse(*base, *arguments))


def test_confirmed_output_on_is_bounded_and_attempts_off() -> None:
    args = parse(
        "--channel", "2",
        "--set-voltage", "2",
        "--set-current", "1",
        "--output-on-for", "0.25",
        "--safety-max-voltage", "3",
        "--safety-max-current", "2",
        "--safety-max-power", "3",
        "--confirm-write", WRITE_CONFIRMATION,
        "--confirm-output-on", OUTPUT_ON_CONFIRMATION,
    )
    driver = RecordingDriver()
    sleeps: list[float] = []
    run_check(
        args,
        driver_factory=lambda _port, _timeout: driver,
        sleep=sleeps.append,
        emit=lambda _message: None,
    )
    assert sleeps == [0.25]
    assert driver.operations[:6] == [
        ("connect",),
        ("identify",),
        ("set_voltage", 2, 2.0),
        ("set_current", 2, 1.0),
        ("output_on", 2),
        ("output_off", 2),
    ]
    assert driver.operations[-1] == ("disconnect",)


def test_output_on_attempts_off_and_disconnect_when_dwell_fails() -> None:
    args = parse(
        "--channel", "1", "--set-voltage", "1", "--set-current", "0.1",
        "--output-on-for", "0.1", "--safety-max-voltage", "1",
        "--safety-max-current", "0.1", "--safety-max-power", "0.1",
        "--confirm-write", WRITE_CONFIRMATION,
        "--confirm-output-on", OUTPUT_ON_CONFIRMATION,
    )
    driver = RecordingDriver()

    def fail_sleep(_duration: float) -> None:
        raise RuntimeError("injected dwell failure")

    with pytest.raises(RuntimeError, match="dwell"):
        run_check(
            args,
            driver_factory=lambda _port, _timeout: driver,
            sleep=fail_sleep,
            emit=lambda _message: None,
        )
    assert ("output_on", 1) in driver.operations
    assert ("output_off", 1) in driver.operations
    assert driver.operations[-1] == ("disconnect",)


def test_output_off_requires_write_confirmation_but_not_output_on_confirmation() -> None:
    args = parse(
        "--channel", "1",
        "--output-off",
        "--confirm-write", WRITE_CONFIRMATION,
    )
    driver = RecordingDriver()
    run_check(args, driver_factory=lambda _port, _timeout: driver, emit=lambda _message: None)
    assert ("output_off", 1) in driver.operations
    assert not any(operation[0] == "output_on" for operation in driver.operations)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration


class MonitoredDriver(RecordingDriver):
    def __init__(self) -> None:
        super().__init__()
        self.output_states = {1: False, 2: False}
        self.programmed_voltage = {1: 5.0, 2: 2.0}
        self.programmed_current = {1: 0.01, 2: 0.1}
        self.measurement_voltage = {1: 5.0, 2: 0.0}
        self.measurement_current = {1: 0.005, 2: 0.0}
        self.scripted: dict[tuple[str, int], deque[object]] = {}
        self.output_on_error: BaseException | None = None
        self.output_off_error: BaseException | None = None
        self.identity = "THURLBY THANDAR,QPX600DP,123,1.00"

    def identify(self) -> str:
        self.operations.append(("identify",))
        return self.identity

    def script(self, name: str, channel: int, *values: object) -> None:
        self.scripted[(name, channel)] = deque(values)

    def _value(self, name: str, channel: int, default: object) -> object:
        values = self.scripted.get((name, channel))
        value = values.popleft() if values else default
        if isinstance(value, BaseException):
            raise value
        return value

    def output_on(self, channel: int) -> None:
        self.operations.append(("output_on", channel))
        self.output_states[channel] = True
        if self.output_on_error is not None:
            raise self.output_on_error

    def output_off(self, channel: int) -> None:
        self.operations.append(("output_off", channel))
        if self.output_off_error is not None:
            raise self.output_off_error
        self.output_states[channel] = False

    def read_voltage_range(self, channel: int) -> SimpleNamespace:
        self.operations.append(("read_voltage_range", channel))
        return SimpleNamespace(
            name="80 V",
            programming_resolution_v=0.002,
            measurement_resolution_v=0.002,
        )

    def read_set_voltage(self, channel: int) -> float:
        self.operations.append(("read_set_voltage", channel))
        return float(self._value("read_set_voltage", channel, self.programmed_voltage[channel]))

    def read_set_current(self, channel: int) -> float:
        self.operations.append(("read_set_current", channel))
        return float(self._value("read_set_current", channel, self.programmed_current[channel]))

    def read_output_state(self, channel: int) -> bool:
        self.operations.append(("read_output_state", channel))
        return bool(self._value("read_output_state", channel, self.output_states[channel]))

    def read_voltage(self, channel: int) -> float:
        self.operations.append(("read_voltage", channel))
        default = self.measurement_voltage[channel] if self.output_states[channel] else 0.0
        return float(self._value("read_voltage", channel, default))

    def read_current(self, channel: int) -> float:
        self.operations.append(("read_current", channel))
        default = self.measurement_current[channel] if self.output_states[channel] else 0.0
        return float(self._value("read_current", channel, default))


def monitored_args(*arguments: str) -> Namespace:
    base = (
        "--channel", "1",
        "--monitor-output-for", "0.2",
        "--expected-voltage", "5",
        "--expected-current-limit", "0.01",
        "--max-measured-current", "0.01",
        "--min-measured-voltage", "4.5",
        "--max-measured-voltage", "5.5",
        "--poll-interval", "0.1",
        "--voltage-settle-time", "0.1",
        "--confirm-write", WRITE_CONFIRMATION,
        "--confirm-monitored-output-on", MONITORED_OUTPUT_CONFIRMATION,
    )
    return parse(*base, *arguments)


def run_monitored(driver: MonitoredDriver, args: Namespace | None = None) -> list[str]:
    clock = FakeClock()
    output: list[str] = []
    run_check(
        args or monitored_args(),
        driver_factory=lambda _port, _timeout: driver,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        timestamp=lambda: "2026-01-02T03:04:05.000+00:00",
        emit=output.append,
    )
    return output


def test_monitored_output_success_polls_and_reports_timestamped_samples() -> None:
    driver = MonitoredDriver()
    output = run_monitored(driver)
    assert driver.operations[:8] == [
        ("connect",), ("identify",),
        ("read_output_state", 1), ("read_output_state", 2),
        ("read_set_voltage", 1), ("read_set_current", 1),
        ("read_voltage_range", 1), ("read_voltage", 1),
    ]
    assert driver.operations.count(("output_on", 1)) == 1
    assert driver.operations.count(("output_off", 1)) == 1
    assert not any(op[0] in {"set_voltage", "set_current"} for op in driver.operations)
    assert not any(op[0] in {"output_on", "output_off"} and op[1] == 2
                   for op in driver.operations)
    assert any(line.startswith("Timestamp (UTC)") for line in output)
    assert sum(line.startswith("2026-01-02T03:04:05.000+00:00") for line in output) == 3
    assert "Final output verification: OP1=OFF; OP2=OFF" in output
    assert driver.operations[-1] == ("disconnect",)


@pytest.mark.parametrize("channel", [1, 2])
def test_monitored_output_rejects_either_output_on_during_preflight(channel: int) -> None:
    driver = MonitoredDriver()
    driver.output_states[channel] = True
    with pytest.raises(MonitoredOutputSafetyError, match="both outputs OFF"):
        run_monitored(driver)
    assert not any(op[0] == "output_on" for op in driver.operations)
    assert driver.operations[-1] == ("disconnect",)


def test_monitored_output_rejects_non_exact_identity_before_output() -> None:
    driver = MonitoredDriver()
    driver.identity = "OTHER MAKER,QPX600DP,123,1.00"
    with pytest.raises(MonitoredOutputSafetyError, match="exact supported"):
        run_monitored(driver)
    assert not any(op[0] == "output_on" for op in driver.operations)
    assert driver.operations[-1] == ("disconnect",)


@pytest.mark.parametrize(
    "attribute,value,match",
    [("programmed_voltage", 4.9, "Programmed voltage"),
     ("programmed_current", 0.03, "Current limit")],
)
def test_monitored_output_rejects_setpoint_mismatch(
    attribute: str, value: float, match: str,
) -> None:
    driver = MonitoredDriver()
    getattr(driver, attribute)[1] = value
    with pytest.raises(MonitoredOutputSafetyError, match=match):
        run_monitored(driver)
    assert not any(op[0] == "output_on" for op in driver.operations)


def test_monitored_output_stops_at_overcurrent_and_attempts_off() -> None:
    driver = MonitoredDriver()
    driver.measurement_current[1] = 0.01
    with pytest.raises(MonitoredOutputSafetyError, match="reached or exceeded"):
        run_monitored(driver)
    assert driver.operations.count(("output_on", 1)) == 1
    assert driver.operations.count(("output_off", 1)) == 1
    assert driver.output_states[1] is False
    assert driver.operations[-1] == ("disconnect",)


def test_monitored_output_allows_initial_zero_then_normal_voltage() -> None:
    driver = MonitoredDriver()
    driver.script("read_voltage", 1, 0.0, 0.0, 5.0, 5.0)
    output = run_monitored(driver)
    samples = [line for line in output if line.startswith("2026-")]
    assert "| 0 | 0.005 | ON | SETTLING" in samples[0]
    assert "| 5 | 0.005 | ON | MONITORING" in samples[1]
    assert driver.output_states[1] is False


def test_monitored_output_stops_on_persistent_low_voltage_after_settling() -> None:
    driver = MonitoredDriver()
    driver.measurement_voltage[1] = 0.0
    with pytest.raises(MonitoredOutputSafetyError, match="below.*after settling"):
        run_monitored(driver)
    assert driver.operations.count(("output_off", 1)) == 1
    assert driver.output_states[1] is False


def test_monitored_output_stops_on_immediate_over_voltage_during_settling() -> None:
    driver = MonitoredDriver()
    driver.measurement_voltage[1] = 5.51
    clock = FakeClock()
    with pytest.raises(MonitoredOutputSafetyError, match="exceeds"):
        run_check(
            monitored_args(),
            driver_factory=lambda _port, _timeout: driver,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            timestamp=lambda: "timestamp",
            emit=lambda _message: None,
        )
    assert clock.now == 0.0
    assert driver.output_states[1] is False


def test_monitored_output_stops_on_under_voltage_when_settle_time_is_zero() -> None:
    driver = MonitoredDriver()
    driver.measurement_voltage[1] = 4.49
    with pytest.raises(MonitoredOutputSafetyError, match="below"):
        run_monitored(driver, monitored_args("--voltage-settle-time", "0"))
    assert driver.output_states[1] is False


def test_monitored_output_stops_if_output_unexpectedly_turns_off() -> None:
    driver = MonitoredDriver()
    driver.script("read_output_state", 1, False, False, False)
    with pytest.raises(MonitoredOutputSafetyError, match="unexpectedly"):
        run_monitored(driver)
    assert driver.operations.count(("output_off", 1)) == 1


@pytest.mark.parametrize(
    "error",
    [InstrumentProtocolError("malformed measurement"), InstrumentTimeoutError("timeout")],
)
def test_monitored_output_measurement_failure_attempts_off_and_closes(
    error: BaseException,
) -> None:
    driver = MonitoredDriver()
    driver.script("read_voltage", 1, 0.0, error)
    with pytest.raises(type(error), match=str(error)):
        run_monitored(driver)
    assert driver.operations.count(("output_off", 1)) == 1
    assert driver.operations[-1] == ("disconnect",)


def test_monitored_output_communication_failure_during_settling_cleans_up() -> None:
    driver = MonitoredDriver()
    driver.script("read_current", 1, 0.0, InstrumentTimeoutError("settling timeout"))
    with pytest.raises(InstrumentTimeoutError, match="settling timeout"):
        run_monitored(driver)
    assert driver.operations.count(("output_off", 1)) == 1
    assert driver.output_states[1] is False
    assert driver.operations[-1] == ("disconnect",)


def test_voltage_settling_does_not_extend_total_output_on_duration() -> None:
    driver = MonitoredDriver()
    clock = FakeClock()
    output: list[str] = []
    run_check(
        monitored_args(
            "--monitor-output-for", "0.2",
            "--voltage-settle-time", "0.15",
            "--poll-interval", "0.1",
        ),
        driver_factory=lambda _port, _timeout: driver,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        timestamp=lambda: "timestamp",
        emit=output.append,
    )
    assert clock.sleeps == [0.1, 0.1]
    assert clock.now == pytest.approx(0.2)
    assert "CH1 elapsed Output-On monitoring time: 0.200 s" in output
    assert driver.operations.count(("output_on", 1)) == 1
    assert driver.operations.count(("output_off", 1)) == 1


def test_monitored_output_failure_during_output_on_never_retries_and_attempts_off() -> None:
    driver = MonitoredDriver()
    driver.output_on_error = InstrumentTimeoutError("ambiguous Output On write")
    with pytest.raises(InstrumentTimeoutError, match="ambiguous"):
        run_monitored(driver)
    assert driver.operations.count(("output_on", 1)) == 1
    assert driver.operations.count(("output_off", 1)) == 1
    assert driver.output_states[1] is False
    assert driver.operations[-1] == ("disconnect",)


def test_monitored_output_reports_when_off_cannot_be_confirmed() -> None:
    driver = MonitoredDriver()
    driver.output_off_error = InstrumentTimeoutError("OFF write failed")
    driver.script("read_output_state", 1, False, True, True)
    output: list[str] = []
    clock = FakeClock()
    with pytest.raises(MonitoredOutputSafetyError, match="could not be confirmed"):
        run_check(
            monitored_args(),
            driver_factory=lambda _port, _timeout: driver,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            timestamp=lambda: "timestamp",
            emit=output.append,
        )
    assert driver.operations.count(("output_on", 1)) == 1
    assert driver.operations.count(("output_off", 1)) == 1
    assert any("physical OFF" in line for line in output)
    assert driver.operations[-1] == ("disconnect",)


@pytest.mark.parametrize("arguments,match", [
    (("--monitor-output-for", "0"), "duration"),
    (("--monitor-output-for", str(MAX_MONITORED_DURATION_S + 1)), "duration"),
    (("--monitor-output-for", "nan"), "finite"),
    (("--poll-interval", "0"), "Poll interval"),
    (("--voltage-settle-time", "nan"), "finite"),
    (("--voltage-settle-time", "-0.1"), "settling time"),
    (("--voltage-settle-time", "5.1"), "settling time"),
    (("--monitor-output-for", "1", "--voltage-settle-time", "2"), "total"),
    (("--max-measured-current", "0"), "current"),
    (("--min-measured-voltage", "6", "--max-measured-voltage", "5"), "Minimum"),
    (("--set-voltage", "5"), "does not accept"),
    (("--confirm-monitored-output-on", "wrong"), "MONITOR-QPX600DP-OUTPUT"),
])
def test_monitored_cli_validation_happens_before_driver_creation(
    arguments: tuple[str, ...], match: str,
) -> None:
    args = monitored_args(*arguments)
    created = False

    def factory(_port: str, _timeout: float) -> RecordingDriver:
        nonlocal created
        created = True
        return RecordingDriver()

    with pytest.raises(HardwareCheckAuthorizationError, match=match):
        run_check(args, driver_factory=factory)
    assert created is False


def test_monitored_cli_requires_every_explicit_value_and_both_tokens() -> None:
    args = parse(
        "--channel", "1",
        "--monitor-output-for", "1",
        "--confirm-write", WRITE_CONFIRMATION,
        "--confirm-monitored-output-on", MONITORED_OUTPUT_CONFIRMATION,
    )
    with pytest.raises(HardwareCheckAuthorizationError, match="requires"):
        validate_authorization(args)

    args = monitored_args("--confirm-write", "wrong")
    with pytest.raises(HardwareCheckAuthorizationError, match="confirm-write"):
        validate_authorization(args)


class OfflineInstrumentSerial:
    """Stateful fake serial endpoint; it never opens or discovers a system port."""

    def __init__(self) -> None:
        self.is_open = True
        self.writes: list[bytes] = []
        self.pending_response = b""
        self.output_states = {1: False, 2: False}

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        command = data.decode("ascii").rstrip("\n")
        if command == "OP1 1":
            self.output_states[1] = True
        elif command == "OP1 0":
            self.output_states[1] = False
        elif command == "OP2 1":
            self.output_states[2] = True
        elif command == "OP2 0":
            self.output_states[2] = False
        elif command.endswith("?"):
            self.pending_response = (self._response(command) + "\r\n").encode("ascii")
        return len(data)

    def _response(self, command: str) -> str:
        responses = {
            "*IDN?": "THURLBY THANDAR,QPX600DP,123,1.00",
            "V1?": "V1 5.000",
            "I1?": "I1 0.01",
            "V2?": "V2 2.000",
            "I2?": "I2 0.10",
            "VRANGE1?": "2",
            "VRANGE2?": "2",
            "*STB?": "0",
        }
        if command in responses:
            return responses[command]
        if command in {"OP1?", "OP2?"}:
            return "1" if self.output_states[int(command[2])] else "0"
        if command in {"V1O?", "V2O?"}:
            channel = int(command[1])
            return "5.000V" if channel == 1 and self.output_states[channel] else "0.000V"
        if command in {"I1O?", "I2O?"}:
            channel = int(command[1])
            return "0.005A" if channel == 1 and self.output_states[channel] else "0.000A"
        raise AssertionError(f"Unexpected fake command: {command}")

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        response = self.pending_response
        self.pending_response = b""
        return response

    def close(self) -> None:
        self.is_open = False


def test_monitored_output_fake_serial_exact_command_sequence_and_closure() -> None:
    fake = OfflineInstrumentSerial()
    factory_calls: list[dict[str, object]] = []

    def serial_factory(**settings: object) -> OfflineInstrumentSerial:
        factory_calls.append(settings)
        return fake

    def driver_factory(port: str, timeout: float) -> QPX600DPDriver:
        return QPX600DPDriver(SerialTransport(
            port,
            timeout_s=timeout,
            write_timeout_s=timeout,
            serial_factory=serial_factory,
        ))

    clock = FakeClock()
    args = monitored_args("--monitor-output-for", "0.1")
    run_check(
        args,
        driver_factory=driver_factory,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        timestamp=lambda: "timestamp",
        emit=lambda _message: None,
    )
    assert len(factory_calls) == 1
    assert factory_calls[0]["port"] == "COM8"
    assert fake.writes == [f"{command}\n".encode("ascii") for command in [
        "*IDN?", "OP1?", "OP2?", "V1?", "I1?", "VRANGE1?", "V1O?", "I1O?",
        "OP1 1",
        "OP1?", "V1O?", "I1O?",
        "OP1?", "V1O?", "I1O?",
        "OP1 0", "OP1?",
        "OP1?", "OP2?",
        "VRANGE1?", "V1?", "I1?", "OP1?", "V1O?", "I1O?",
        "VRANGE2?", "V2?", "I2?", "OP2?", "V2O?", "I2O?",
        "*STB?",
    ]]
    assert fake.output_states == {1: False, 2: False}
    assert fake.is_open is False


class StatusForensicsSerial:
    """Scripted query-only endpoint for the destructive one-shot status path."""

    def __init__(self, *, output1_on: bool = False) -> None:
        self.is_open = True
        self.writes: list[bytes] = []
        self.pending_response = b""
        self.close_calls = 0
        self.responses = {
            "*IDN?": "THURLBY THANDAR,QPX600DP,SECRET-SERIAL,1.20",
            "*STB?": "33",
            "LSE1?": "24",
            "LSE2?": "1",
            "*ESE?": "20",
            "OVP1?": "VP1 6.000",
            "OCP1?": "CP1 0.10",
            "OVP2?": "VP2 7.000",
            "OCP2?": "CP2 0.20",
            "VRANGE1?": "2",
            "V1?": "V1 4.000",
            "I1?": "I1 0.08",
            "OP1?": "1" if output1_on else "0",
            "V1O?": "0.000V",
            "I1O?": "0.000A",
            "VRANGE2?": "2",
            "V2?": "V2 4.000",
            "I2?": "I2 0.08",
            "OP2?": "0",
            "V2O?": "0.004V",
            "I2O?": "0.001A",
            "LSR1?": "24",
            "LSR2?": "1",
            "*ESR?": "20",
            "EER?": "103",
            "QER?": "1",
        }

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        command = data.decode("ascii").rstrip("\n")
        assert command.endswith("?"), f"unexpected mutation command: {command}"
        self.pending_response = (self.responses[command] + "\r\n").encode("ascii")
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        response = self.pending_response
        self.pending_response = b""
        return response

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False


def _status_forensics_args() -> Namespace:
    return parse("--capture-status-once", STATUS_FORENSICS_CONFIRMATION)


def _status_driver_factory(fake: StatusForensicsSerial):
    def factory(port: str, timeout: float) -> QPX600DPDriver:
        return QPX600DPDriver(SerialTransport(
            port,
            timeout_s=timeout,
            write_timeout_s=timeout,
            serial_factory=lambda **_settings: fake,
        ))

    return factory


def test_status_forensics_fake_serial_exact_order_decoding_and_report(
    tmp_path: Path,
) -> None:
    fake = StatusForensicsSerial()
    output: list[str] = []
    run_check(
        _status_forensics_args(),
        driver_factory=_status_driver_factory(fake),
        timestamp=lambda: "2026-10-08T12:34:56.789+00:00",
        emit=output.append,
        report_directory=tmp_path,
    )
    commands = [data.decode("ascii").rstrip("\n") for data in fake.writes]
    assert commands == [
        "*IDN?", "*STB?", "LSE1?", "LSE2?", "*ESE?",
        "OVP1?", "OCP1?", "OVP2?", "OCP2?",
        "VRANGE1?", "V1?", "I1?", "OP1?", "V1O?", "I1O?",
        "VRANGE2?", "V2?", "I2?", "OP2?", "V2O?", "I2O?",
        "LSR1?", "LSR2?", "*ESR?", "EER?", "QER?",
    ]
    for command in ("LSR1?", "LSR2?", "*ESR?", "EER?", "QER?"):
        assert commands.count(command) == 1
    assert all(command.endswith("?") for command in commands)
    assert fake.close_calls == 1
    assert not fake.is_open

    report_files = list(tmp_path.glob("qpx600dp_status_forensics_*.txt"))
    assert len(report_files) == 1
    report = report_files[0].read_text(encoding="utf-8")
    assert "SECRET-SERIAL" not in report
    assert "[SERIAL REDACTED]" in report
    assert "bit 4=1 (over-current trip)" in report
    assert "bit 3=1 (over-voltage trip)" in report
    assert "bit 4=1 (execution error)" in report
    assert "EER? = 103; command invalid in the present circumstances" in report
    assert "QER? = 1; interrupted query" in report
    assert "consume and clear their respective evidence" in report
    assert any("Diagnostic report:" in line for line in output)


def test_status_forensics_output_on_stops_before_clear_on_read_queries(
    tmp_path: Path,
) -> None:
    fake = StatusForensicsSerial(output1_on=True)
    with pytest.raises(StatusForensicsSafetyError, match="both outputs must be OFF"):
        run_check(
            _status_forensics_args(),
            driver_factory=_status_driver_factory(fake),
            timestamp=lambda: "2026-10-08T12:35:00.000+00:00",
            emit=lambda _message: None,
            report_directory=tmp_path,
        )
    commands = [data.decode("ascii").rstrip("\n") for data in fake.writes]
    assert not {"LSR1?", "LSR2?", "*ESR?", "EER?", "QER?"}.intersection(commands)
    assert fake.close_calls == 1
    report = next(tmp_path.glob("qpx600dp_status_forensics_*.txt")).read_text(
        encoding="utf-8"
    )
    assert "ABORTED: both outputs must be OFF" in report
    assert "SECRET-SERIAL" not in report


def test_status_forensics_malformed_preflight_stops_without_retry_or_clear(
    tmp_path: Path,
) -> None:
    fake = StatusForensicsSerial()
    fake.responses["LSE1?"] = "bad"
    with pytest.raises(InstrumentProtocolError, match="LSE1"):
        run_check(
            _status_forensics_args(),
            driver_factory=_status_driver_factory(fake),
            emit=lambda _message: None,
            report_directory=tmp_path,
        )
    commands = [data.decode("ascii").rstrip("\n") for data in fake.writes]
    assert commands == ["*IDN?", "*STB?", "LSE1?"]
    assert fake.close_calls == 1
    assert list(tmp_path.iterdir()) == []


def test_status_forensics_identity_mismatch_stops_after_one_query(
    tmp_path: Path,
) -> None:
    fake = StatusForensicsSerial()
    fake.responses["*IDN?"] = "OTHER MAKER,QPX600DP,SECRET-SERIAL,1.20"
    with pytest.raises(StatusForensicsSafetyError, match="exact supported"):
        run_check(
            _status_forensics_args(),
            driver_factory=_status_driver_factory(fake),
            emit=lambda _message: None,
            report_directory=tmp_path,
        )
    assert fake.writes == [b"*IDN?\n"]
    assert fake.close_calls == 1
    assert list(tmp_path.iterdir()) == []
