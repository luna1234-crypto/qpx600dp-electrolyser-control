"""Offline QPX600DP driver tests using a scripted serial port."""

from collections import deque

import pytest

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.exceptions import (
    InstrumentNotConnectedError,
    InstrumentProtocolError,
    InstrumentTimeoutError,
    InstrumentValueError,
)
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.serial_transport import SerialTransport


class ScriptedSerial:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = deque(responses)
        self.writes: list[bytes] = []
        self.is_open = True
        self.close_calls = 0

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        return self.responses.popleft() if self.responses else b""

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False


def connected_driver(responses: list[bytes]) -> tuple[QPX600DPDriver, ScriptedSerial]:
    fake = ScriptedSerial(responses)
    transport = SerialTransport("COM11", serial_factory=lambda **_settings: fake)
    driver = QPX600DPDriver(transport)
    driver.connect()
    return driver, fake


def test_driver_implements_interface_and_lifecycle_sends_no_commands() -> None:
    fake = ScriptedSerial([])
    transport = SerialTransport("COM11", serial_factory=lambda **_settings: fake)
    driver: PowerSupplyInterface = QPX600DPDriver(transport)
    assert driver.capabilities.number_of_channels == 2
    assert not driver.is_connected()
    driver.connect()
    driver.connect()
    assert driver.is_connected()
    assert fake.writes == []
    driver.disconnect()
    driver.disconnect()
    assert not driver.is_connected()
    assert fake.close_calls == 1
    assert fake.writes == []


@pytest.mark.parametrize("third_field", ["0", "279730", "SIMULATED-LIKE-TEXT"])
def test_identify_accepts_documented_third_field_ambiguity(third_field: str) -> None:
    response = f"THURLBY THANDAR, QPX600DP, {third_field}, 1.00\r\n".encode("ascii")
    driver, fake = connected_driver([response])
    assert driver.identify() == response[:-2].decode("ascii")
    assert fake.writes == [b"*IDN?\n"]


@pytest.mark.parametrize("response", [
    b"THURLBY THANDAR,QPX600DP,1.00\r\n",
    b"THURLBY THANDAR,CPX400DP,123,1.00\r\n",
    b",QPX600DP,123,1.00\r\n",
])
def test_identify_rejects_malformed_or_wrong_model(response: bytes) -> None:
    driver, _ = connected_driver([response])
    with pytest.raises(InstrumentProtocolError):
        driver.identify()
    assert driver.is_connected()  # Framing is intact; caller decides whether to disconnect.


def test_voltage_set_uses_current_range_and_exact_channel_bytes() -> None:
    driver, fake = connected_driver([b"1\r\n", b"2\r\n"])
    driver.set_voltage(1, 60)
    driver.set_voltage(2, 79.998)
    assert fake.writes == [
        b"VRANGE1?\n", b"V1 60.000\n",
        b"VRANGE2?\n", b"V2 79.998\n",
    ]


@pytest.mark.parametrize("response,value", [
    (b"1\r\n", 60.001),
    (b"2\r\n", 79.999),
    (b"2\r\n", 80.002),
])
def test_invalid_voltage_is_rejected_after_read_only_range_query(
    response: bytes, value: float,
) -> None:
    driver, fake = connected_driver([response])
    with pytest.raises(InstrumentValueError):
        driver.set_voltage(1, value)
    assert fake.writes == [b"VRANGE1?\n"]


@pytest.mark.parametrize("channel,value,expected", [
    (1, 0.01, b"I1 0.01\n"),
    (2, 50, b"I2 50.00\n"),
])
def test_current_set_uses_exact_grid_and_channel(
    channel: int, value: float, expected: bytes,
) -> None:
    driver, fake = connected_driver([])
    driver.set_current(channel, value)
    assert fake.writes == [expected]


@pytest.mark.parametrize("value", [0, 0.001, 50.01, 1.001, float("nan"), True])
def test_invalid_current_never_writes(value: object) -> None:
    driver, fake = connected_driver([])
    with pytest.raises(InstrumentValueError):
        driver.set_current(1, value)  # type: ignore[arg-type]
    assert fake.writes == []


def test_setpoint_measurement_state_range_and_status_parsing() -> None:
    driver, fake = connected_driver([
        b"V1 12.345\r\n",
        b"I2  4.50\r\n",
        b"12.346V\r\n",
        b"4.49A\r\n",
        b"1\r\n",
        b"2\r\n",
        b"68\r\n",
    ])
    assert driver.read_set_voltage(1) == pytest.approx(12.345)
    assert driver.read_set_current(2) == pytest.approx(4.5)
    assert driver.read_voltage(1) == pytest.approx(12.346)
    assert driver.read_current(2) == pytest.approx(4.49)
    assert driver.read_output_state(1)
    assert driver.read_voltage_range(2).name == "80 V"
    assert driver.read_status_byte() == 68
    assert fake.writes == [
        b"V1?\n", b"I2?\n", b"V1O?\n", b"I2O?\n",
        b"OP1?\n", b"VRANGE2?\n", b"*STB?\n",
    ]


def test_status_forensics_queries_parse_documented_responses_exactly_once() -> None:
    driver, fake = connected_driver([
        b"17\r\n", b"34\r\n", b"VP1 6.500\r\n", b"CP2 0.25\r\n",
        b"24\r\n", b"5\r\n", b"20\r\n", b"103\r\n", b"1\r\n",
    ])
    assert driver.read_limit_status_enable(1) == 17
    assert driver.read_standard_event_status_enable() == 34
    assert driver.read_over_voltage_trip_setting(1) == pytest.approx(6.5)
    assert driver.read_over_current_trip_setting(2) == pytest.approx(0.25)
    assert driver.read_limit_status_and_clear(1) == 24
    assert driver.read_limit_status_and_clear(2) == 5
    assert driver.read_standard_event_status_and_clear() == 20
    assert driver.read_execution_error_and_clear() == 103
    assert driver.read_query_error_and_clear() == 1
    assert fake.writes == [
        b"LSE1?\n", b"*ESE?\n", b"OVP1?\n", b"OCP2?\n",
        b"LSR1?\n", b"LSR2?\n", b"*ESR?\n", b"EER?\n", b"QER?\n",
    ]


@pytest.mark.parametrize("method", [
    "read_limit_status_enable",
    "read_limit_status_and_clear",
])
def test_channel_status_queries_reject_invalid_channel_without_writing(method: str) -> None:
    driver, fake = connected_driver([])
    with pytest.raises(InstrumentValueError):
        getattr(driver, method)(3)
    assert fake.writes == []


def test_output_commands_are_channel_specific_and_never_implicit() -> None:
    driver, fake = connected_driver([])
    driver.output_on(2)
    driver.output_off(1)
    assert fake.writes == [b"OP2 1\n", b"OP1 0\n"]


@pytest.mark.parametrize("response", [b"0\r\n", b"2\r\n", b"bad\r\n"])
def test_operation_complete_requires_documented_one(response: bytes) -> None:
    driver, fake = connected_driver([response])
    with pytest.raises(InstrumentProtocolError, match=r"\*OPC\?"):
        driver.confirm_operation_complete()
    assert fake.writes == [b"*OPC?\n"]


def test_operation_complete_accepts_documented_one() -> None:
    driver, fake = connected_driver([b"1\r\n"])
    driver.confirm_operation_complete()
    assert fake.writes == [b"*OPC?\n"]


@pytest.mark.parametrize("method,response", [
    ("read_set_voltage", b"V2 1.000\r\n"),
    ("read_set_current", b"I1 1e1\r\n"),
    ("read_voltage", b"1.000 A\r\n"),
    ("read_current", b"-0.01A\r\n"),
    ("read_output_state", b"ON\r\n"),
    ("read_voltage_range", b"3\r\n"),
])
def test_malformed_responses_are_rejected_without_guessing(method: str, response: bytes) -> None:
    driver, _ = connected_driver([response])
    with pytest.raises(InstrumentProtocolError):
        getattr(driver, method)(1)


@pytest.mark.parametrize("method,response", [
    ("read_set_voltage", b"V1 80.001\r\n"),
    ("read_set_current", b"I1 50.01\r\n"),
])
def test_setpoint_responses_above_capability_are_rejected(method: str, response: bytes) -> None:
    driver, _ = connected_driver([response])
    with pytest.raises(InstrumentProtocolError, match="capability"):
        getattr(driver, method)(1)


@pytest.mark.parametrize("response", [b"-1\r\n", b"256\r\n", b"1.0\r\n"])
def test_invalid_status_byte_is_rejected(response: bytes) -> None:
    driver, _ = connected_driver([response])
    with pytest.raises(InstrumentProtocolError):
        driver.read_status_byte()


def test_transport_timeout_propagates_and_invalidates_driver_connection() -> None:
    driver, fake = connected_driver([b""])
    with pytest.raises(InstrumentTimeoutError):
        driver.identify()
    assert not driver.is_connected()
    assert fake.close_calls == 1


@pytest.mark.parametrize("channel", [0, 3, -1, True, 1.0, "1"])
def test_invalid_channels_do_not_write(channel: object) -> None:
    driver, fake = connected_driver([])
    operations = (
        driver.set_voltage, driver.set_current, driver.read_set_voltage,
        driver.read_set_current, driver.output_on, driver.output_off,
        driver.read_output_state, driver.read_voltage, driver.read_current,
        driver.read_voltage_range,
    )
    for operation in operations:
        with pytest.raises(InstrumentValueError):
            if operation in (driver.set_voltage, driver.set_current):
                operation(channel, 1)  # type: ignore[misc]
            else:
                operation(channel)  # type: ignore[misc]
    assert fake.writes == []


def test_disconnected_driver_propagates_not_connected_error() -> None:
    transport = SerialTransport("COM11", serial_factory=lambda **_settings: ScriptedSerial([]))
    driver = QPX600DPDriver(transport)
    with pytest.raises(InstrumentNotConnectedError):
        driver.identify()


def test_driver_rejects_non_transport_dependency() -> None:
    with pytest.raises(TypeError):
        QPX600DPDriver(object())  # type: ignore[arg-type]
