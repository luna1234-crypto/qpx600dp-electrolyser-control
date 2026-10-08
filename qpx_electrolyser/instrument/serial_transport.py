"""Bounded QPX600DP serial/USB-VCP transport.

Issue 14 printed pp. 21-23 specify 9600 baud, 8N1 and XON/XOFF for RS232;
USB exposes a virtual COM port and ignores those line settings. Printed p. 31
requires LF-terminated ASCII commands and CRLF-terminated responses.
"""

from collections.abc import Callable
import json
import logging
import re
from threading import Lock
import time
from typing import Protocol, runtime_checkable

from qpx_electrolyser.instrument.exceptions import (
    InstrumentConnectionError,
    InstrumentNotConnectedError,
    InstrumentProtocolError,
    InstrumentTimeoutError,
    InstrumentTransportError,
    InstrumentValueError,
)
from qpx_electrolyser.instrument.transaction_diagnostics import (
    current_transaction_context,
)


COMMAND_TERMINATOR = b"\n"
RESPONSE_TERMINATOR = b"\r\n"
_OUTPUT_OFF_COMMAND = re.compile(r"OP[12] 0")
_LOGGER = logging.getLogger("qpx_electrolyser.instrument.serial_transport")


@runtime_checkable
class SerialPort(Protocol):
    """Subset of ``serial.Serial`` used by the transport and fake tests."""

    is_open: bool

    def write(self, data: bytes) -> int: ...
    def flush(self) -> None: ...
    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes: ...
    def close(self) -> None: ...


SerialFactory = Callable[..., SerialPort]


def _open_pyserial(**settings: object) -> SerialPort:
    import serial

    return serial.Serial(**settings)


class SerialTransport:
    """One explicit serial port with bounded writes and one-response queries.

    The class never enumerates ports, clears buffers or writes during connect.
    A timeout or framing failure invalidates the session because the next reply
    can no longer be paired safely with a future query.
    """

    def __init__(
        self,
        port: str,
        *,
        timeout_s: float = 1.0,
        write_timeout_s: float = 1.0,
        max_response_bytes: int = 512,
        serial_factory: SerialFactory = _open_pyserial,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise InstrumentValueError("port must be a nonempty explicit serial-port name.")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise InstrumentValueError("timeout_s must be a positive number.")
        if (
            isinstance(write_timeout_s, bool)
            or not isinstance(write_timeout_s, (int, float))
            or write_timeout_s <= 0
        ):
            raise InstrumentValueError("write_timeout_s must be a positive number.")
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise InstrumentValueError("max_response_bytes must be a positive integer.")
        if not callable(serial_factory):
            raise InstrumentValueError("serial_factory must be callable.")
        self._port_name = port.strip()
        self._timeout_s = float(timeout_s)
        self._write_timeout_s = float(write_timeout_s)
        self._max_response_bytes = max_response_bytes
        self._serial_factory = serial_factory
        self._serial: SerialPort | None = None
        self._transaction_lock = Lock()

    @property
    def port_name(self) -> str:
        return self._port_name

    def connect(self) -> None:
        """Open the named port without sending bytes or changing instrument state."""
        with self._transaction_lock:
            if self.is_connected():
                return
            self._serial = None
            try:
                candidate = self._serial_factory(
                    port=self._port_name,
                    baudrate=9600,
                    bytesize=8,
                    parity="N",
                    stopbits=1,
                    timeout=self._timeout_s,
                    write_timeout=self._write_timeout_s,
                    xonxoff=True,
                    rtscts=False,
                    dsrdtr=False,
                )
            except Exception as error:
                raise InstrumentConnectionError(
                    f"Could not open serial port {self._port_name!r}."
                ) from error
            if not getattr(candidate, "is_open", False):
                try:
                    candidate.close()
                except Exception:
                    pass
                raise InstrumentConnectionError(
                    f"Serial port {self._port_name!r} did not open."
                )
            self._serial = candidate

    def disconnect(self) -> None:
        """Close locally; never send reset, LOCAL, output or unlock commands."""
        with self._transaction_lock:
            serial_port = self._serial
            self._serial = None
            if serial_port is None:
                return
            try:
                if getattr(serial_port, "is_open", False):
                    serial_port.close()
            except Exception as error:
                raise InstrumentConnectionError(
                    f"Serial port {self._port_name!r} did not close cleanly."
                ) from error

    def is_connected(self) -> bool:
        serial_port = self._serial
        return serial_port is not None and bool(getattr(serial_port, "is_open", False))

    def command(self, command: str) -> None:
        """Send one documented command followed by LF; expect no response."""
        encoded = self._encode_command(command)
        with self._transaction_lock:
            serial_port = self._require_port()
            if _OUTPUT_OFF_COMMAND.fullmatch(command):
                context = current_transaction_context()
                self._log_diagnostic(
                    "output_off_intent",
                    command=command,
                    reason=context.output_off_reason or context.operation_name,
                )
            self._write(serial_port, encoded)
            self._log_diagnostic("serial_tx", command=command)

    def query(self, command: str) -> str:
        """Send one LF-terminated query and return one ASCII payload before CRLF."""
        encoded = self._encode_command(command)
        with self._transaction_lock:
            serial_port = self._require_port()
            self._write(serial_port, encoded)
            self._log_diagnostic("serial_tx", command=command)
            try:
                framed = serial_port.read_until(
                    expected=RESPONSE_TERMINATOR,
                    size=self._max_response_bytes + len(RESPONSE_TERMINATOR),
                )
            except Exception as error:
                self._invalidate(serial_port)
                raise InstrumentTransportError("Serial response read failed.") from error
            self._log_diagnostic(
                "serial_rx",
                command=command,
                raw_response=(
                    "<redacted QPX600DP identity>"
                    if command.strip().upper() == "*IDN?"
                    else repr(framed)
                ),
                redacted=command.strip().upper() == "*IDN?",
            )
            if not framed.endswith(RESPONSE_TERMINATOR):
                self._invalidate(serial_port)
                raise InstrumentTimeoutError(
                    "Timed out before a complete CRLF-terminated response arrived."
                )
            payload = framed[: -len(RESPONSE_TERMINATOR)]
            if len(payload) > self._max_response_bytes or b"\r" in payload or b"\n" in payload:
                self._invalidate(serial_port)
                raise InstrumentProtocolError("Instrument response framing is invalid.")
            try:
                return payload.decode("ascii")
            except UnicodeDecodeError as error:
                self._invalidate(serial_port)
                raise InstrumentProtocolError("Instrument response is not ASCII.") from error

    @staticmethod
    def _log_diagnostic(event: str, **fields: object) -> None:
        context = current_transaction_context()
        record = {
            "event": event,
            "monotonic_s": round(time.monotonic(), 9),
            "session_id": context.session_id,
            "request_id": context.request_id,
            "operation": context.operation_name,
            "priority": context.priority,
            **fields,
        }
        _LOGGER.info("hardware_transaction %s", json.dumps(record, sort_keys=True))

    def _require_port(self) -> SerialPort:
        serial_port = self._serial
        if serial_port is None or not getattr(serial_port, "is_open", False):
            self._serial = None
            raise InstrumentNotConnectedError("Open the serial transport before instrument I/O.")
        return serial_port

    @staticmethod
    def _encode_command(command: str) -> bytes:
        if not isinstance(command, str) or not command:
            raise InstrumentValueError("command must be a nonempty ASCII string.")
        if any(character in command for character in "\r\n;"):
            raise InstrumentValueError("Send exactly one command without terminators or separators.")
        try:
            encoded = command.encode("ascii")
        except UnicodeEncodeError as error:
            raise InstrumentValueError("command must contain ASCII characters only.") from error
        if any(byte < 0x20 or byte > 0x7E for byte in encoded):
            raise InstrumentValueError("command must contain printable ASCII characters only.")
        return encoded + COMMAND_TERMINATOR

    def _write(self, serial_port: SerialPort, encoded: bytes) -> None:
        try:
            written = serial_port.write(encoded)
            if written != len(encoded):
                raise OSError(f"partial serial write: {written}/{len(encoded)} bytes")
            serial_port.flush()
        except Exception as error:
            self._invalidate(serial_port)
            raise InstrumentTransportError("Serial command write failed.") from error

    def _invalidate(self, serial_port: SerialPort) -> None:
        if self._serial is serial_port:
            self._serial = None
        try:
            if getattr(serial_port, "is_open", False):
                serial_port.close()
        except Exception:
            pass
