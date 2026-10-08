"""Offline serial transport tests; no port discovery or hardware access."""

from collections import deque
import json
import logging
from typing import Any

import pytest

from qpx_electrolyser.instrument.exceptions import (
    InstrumentConnectionError,
    InstrumentNotConnectedError,
    InstrumentProtocolError,
    InstrumentTimeoutError,
    InstrumentTransportError,
    InstrumentValueError,
)
from qpx_electrolyser.instrument.serial_transport import SerialTransport
from qpx_electrolyser.instrument.transaction_diagnostics import (
    transaction_diagnostic_context,
)


class FakeSerial:
    def __init__(
        self,
        responses: list[bytes | Exception] | None = None,
        *,
        is_open: bool = True,
    ) -> None:
        self.is_open = is_open
        self.responses = deque(responses or [])
        self.writes: list[bytes] = []
        self.read_calls: list[tuple[bytes, int | None]] = []
        self.flush_calls = 0
        self.close_calls = 0
        self.write_result: int | None = None
        self.write_error: Exception | None = None
        self.close_error: Exception | None = None

    def write(self, data: bytes) -> int:
        if self.write_error is not None:
            raise self.write_error
        self.writes.append(data)
        return len(data) if self.write_result is None else self.write_result

    def flush(self) -> None:
        self.flush_calls += 1

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        self.read_calls.append((expected, size))
        if not self.responses:
            return b""
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False
        if self.close_error is not None:
            raise self.close_error


class CapturingFactory:
    def __init__(self, fake: FakeSerial | None = None, error: Exception | None = None) -> None:
        self.fake = fake if fake is not None else FakeSerial()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **settings: object) -> FakeSerial:
        self.calls.append(settings)
        if self.error is not None:
            raise self.error
        return self.fake


def connected_transport(
    responses: list[bytes | Exception] | None = None,
) -> tuple[SerialTransport, FakeSerial, CapturingFactory]:
    fake = FakeSerial(responses)
    factory = CapturingFactory(fake)
    transport = SerialTransport("COM7", serial_factory=factory)
    transport.connect()
    return transport, fake, factory


def test_connect_uses_documented_serial_settings_and_sends_nothing() -> None:
    transport, fake, factory = connected_transport()
    assert factory.calls == [{
        "port": "COM7",
        "baudrate": 9600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 1.0,
        "write_timeout": 1.0,
        "xonxoff": True,
        "rtscts": False,
        "dsrdtr": False,
    }]
    assert fake.writes == []
    assert transport.is_connected()
    transport.connect()
    assert len(factory.calls) == 1
    assert fake.writes == []


def test_command_and_query_use_exact_terminators() -> None:
    transport, fake, _ = connected_transport([b"THURLBY THANDAR,QPX600DP,0,1.00\r\n"])
    transport.command("OP2 0")
    response = transport.query("*IDN?")
    assert fake.writes == [b"OP2 0\n", b"*IDN?\n"]
    assert fake.flush_calls == 2
    assert fake.read_calls == [(b"\r\n", 514)]
    assert response == "THURLBY THANDAR,QPX600DP,0,1.00"


def test_structured_transaction_diagnostics_include_context_and_redact_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport, _fake, _ = connected_transport([
        b"0\r\n",
        b"THURLBY THANDAR,QPX600DP,SENSITIVE-SERIAL,1.00\r\n",
    ])
    caplog.set_level(
        logging.INFO, logger="qpx_electrolyser.instrument.serial_transport"
    )
    diagnostic_logger = logging.getLogger("qpx_electrolyser.instrument.serial_transport")
    previous_propagate = diagnostic_logger.propagate
    diagnostic_logger.propagate = False
    diagnostic_logger.addHandler(caplog.handler)
    try:
        with transaction_diagnostic_context(
            session_id=7,
            request_id=42,
            operation_name="ch1-constant-voltage-stop",
            priority="SAFETY",
            output_off_reason="Output unexpectedly reported OFF.",
        ):
            transport.command("OP1 0")
            assert transport.query("OP1?") == "0"
            transport.query("*IDN?")
    finally:
        diagnostic_logger.removeHandler(caplog.handler)
        diagnostic_logger.propagate = previous_propagate

    records = [
        json.loads(record.message.removeprefix("hardware_transaction "))
        for record in caplog.records
        if record.message.startswith("hardware_transaction ")
    ]
    assert all(record["session_id"] == 7 for record in records)
    assert all(record["request_id"] == 42 for record in records)
    assert all(record["operation"] == "ch1-constant-voltage-stop" for record in records)
    assert all(record["priority"] == "SAFETY" for record in records)
    assert all(isinstance(record["monotonic_s"], float) for record in records)
    assert records[0]["event"] == "output_off_intent"
    assert records[0]["reason"] == "Output unexpectedly reported OFF."
    assert records[1]["event"] == "serial_tx"
    assert records[1]["command"] == "OP1 0"
    output_state_rx = next(
        record for record in records
        if record["event"] == "serial_rx" and record["command"] == "OP1?"
    )
    assert output_state_rx["raw_response"] == "b'0\\r\\n'"
    identity_rx = next(
        record for record in records
        if record["event"] == "serial_rx" and record["command"] == "*IDN?"
    )
    assert identity_rx["redacted"] is True
    assert identity_rx["raw_response"] == "<redacted QPX600DP identity>"
    assert "SENSITIVE-SERIAL" not in caplog.text


@pytest.mark.parametrize("response", [b"", b"partial", b"value\n"])
def test_incomplete_response_times_out_and_invalidates_session(response: bytes) -> None:
    transport, fake, _ = connected_transport([response])
    with pytest.raises(InstrumentTimeoutError):
        transport.query("*IDN?")
    assert not transport.is_connected()
    assert fake.close_calls == 1
    with pytest.raises(InstrumentNotConnectedError):
        transport.query("*IDN?")


@pytest.mark.parametrize("response", [b"bad\ninside\r\n", b"\xff\r\n"])
def test_bad_framing_or_non_ascii_invalidates_session(response: bytes) -> None:
    transport, fake, _ = connected_transport([response])
    with pytest.raises(InstrumentProtocolError):
        transport.query("*IDN?")
    assert not transport.is_connected()
    assert fake.close_calls == 1


def test_oversized_response_invalidates_session() -> None:
    fake = FakeSerial([b"12345\r\n"])
    transport = SerialTransport("COM7", max_response_bytes=4, serial_factory=CapturingFactory(fake))
    transport.connect()
    with pytest.raises(InstrumentProtocolError, match="framing"):
        transport.query("*STB?")
    assert not transport.is_connected()
    assert fake.close_calls == 1


def test_read_and_write_errors_are_wrapped_and_invalidate_session() -> None:
    transport, fake, _ = connected_transport([OSError("read failed")])
    with pytest.raises(InstrumentTransportError, match="read"):
        transport.query("*IDN?")
    assert not transport.is_connected()

    transport, fake, _ = connected_transport()
    fake.write_error = OSError("write failed")
    with pytest.raises(InstrumentTransportError, match="write"):
        transport.command("OP1 0")
    assert not transport.is_connected()


def test_partial_write_is_an_error_and_session_is_closed() -> None:
    transport, fake, _ = connected_transport()
    fake.write_result = 2
    with pytest.raises(InstrumentTransportError):
        transport.command("OP1 0")
    assert fake.writes == [b"OP1 0\n"]
    assert fake.close_calls == 1
    assert not transport.is_connected()


def test_connect_and_disconnect_failures_are_wrapped_without_stale_state() -> None:
    factory = CapturingFactory(error=OSError("open failed"))
    transport = SerialTransport("COM9", serial_factory=factory)
    with pytest.raises(InstrumentConnectionError, match="COM9"):
        transport.connect()
    assert not transport.is_connected()

    fake = FakeSerial()
    fake.close_error = OSError("close failed")
    transport = SerialTransport("COM9", serial_factory=CapturingFactory(fake))
    transport.connect()
    with pytest.raises(InstrumentConnectionError, match="close"):
        transport.disconnect()
    assert not transport.is_connected()
    transport.disconnect()

    closed = FakeSerial(is_open=False)
    transport = SerialTransport("COM9", serial_factory=CapturingFactory(closed))
    with pytest.raises(InstrumentConnectionError, match="did not open"):
        transport.connect()
    assert closed.close_calls == 1
    assert not transport.is_connected()


def test_disconnected_io_and_invalid_commands_are_rejected_before_writing() -> None:
    transport = SerialTransport("COM7", serial_factory=CapturingFactory())
    with pytest.raises(InstrumentNotConnectedError):
        transport.command("OP1 0")

    transport, fake, _ = connected_transport()
    for command in ("", "OP1 0\n", "OP1 0;OP2 0", "V1 µ"):
        with pytest.raises(InstrumentValueError):
            transport.command(command)
    assert fake.writes == []


@pytest.mark.parametrize("settings", [
    {"port": ""}, {"port": "   "}, {"timeout_s": 0}, {"timeout_s": True},
    {"write_timeout_s": -1}, {"max_response_bytes": 0},
])
def test_invalid_configuration_is_rejected(settings: dict[str, object]) -> None:
    defaults: dict[str, object] = {"port": "COM7", "serial_factory": CapturingFactory()}
    defaults.update(settings)
    with pytest.raises(InstrumentValueError):
        SerialTransport(**defaults)  # type: ignore[arg-type]
