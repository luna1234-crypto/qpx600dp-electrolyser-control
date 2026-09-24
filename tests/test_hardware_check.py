"""Safety gates for the explicit-port hardware check; all tests use a fake driver."""

from argparse import Namespace
from types import SimpleNamespace

import pytest

from qpx_electrolyser.instrument.hardware_check import (
    HardwareCheckAuthorizationError,
    OUTPUT_ON_CONFIRMATION,
    WRITE_CONFIRMATION,
    build_parser,
    run_check,
    validate_authorization,
)


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
