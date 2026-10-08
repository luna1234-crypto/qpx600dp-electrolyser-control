"""QPX600DP command driver grounded only in Issue 14.

Every implemented command cites the printed manual page in its method docstring.
The driver does not reset, select CONFIG, change voltage range, acquire IFLOCK,
alter protection, clear status registers or enable outputs during connection.
"""

import math
import re

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.capabilities import (
    InstrumentCapabilities,
    QPX600DP_CAPABILITIES,
    VoltageRangeCapability,
)
from qpx_electrolyser.instrument.exceptions import InstrumentProtocolError
from qpx_electrolyser.instrument.serial_transport import SerialTransport


_FIXED_NUMBER = r"[+-]?\d+(?:\.\d+)?"


class QPX600DPDriver(PowerSupplyInterface):
    """Synchronous QPX600DP backend for one explicitly named serial transport."""

    def __init__(self, transport: SerialTransport) -> None:
        if not isinstance(transport, SerialTransport):
            raise TypeError("transport must be a SerialTransport instance")
        self._transport = transport

    @property
    def capabilities(self) -> InstrumentCapabilities:
        """Return Issue 14 hardware capabilities (printed pp. 2-4, 11, 13)."""
        return QPX600DP_CAPABILITIES

    def connect(self) -> None:
        """Open transport only; send no command (Issue 14 printed pp. 21-23)."""
        self._transport.connect()

    def disconnect(self) -> None:
        """Close transport only; do not infer or change output state."""
        self._transport.disconnect()

    def is_connected(self) -> bool:
        return self._transport.is_connected()

    def identify(self) -> str:
        """Query `*IDN?` (Issue 14 printed p. 36; example p. 25)."""
        response = self._transport.query("*IDN?")
        parts = [part.strip() for part in response.split(",")]
        if len(parts) != 4 or any(not part for part in parts):
            raise InstrumentProtocolError("Malformed *IDN? response; expected four nonempty fields.")
        if parts[1].upper() != "QPX600DP":
            raise InstrumentProtocolError(
                f"Connected instrument model is {parts[1]!r}, not QPX600DP."
            )
        # Issue 14 p. 36 shows literal 0 in field three, while the p. 25
        # discovery example shows a serial number. Preserve either form.
        return response.strip()

    def set_voltage(self, channel: int, voltage_v: float) -> None:
        """Send `V<N> <NRF>` after querying range (Issue 14 pp. 33-34)."""
        self.capabilities.validate_channel(channel)
        voltage_range = self.read_voltage_range(channel)
        value = self.capabilities.validate_voltage(voltage_v, voltage_range.name)
        self._transport.command(f"V{channel} {value:.3f}")

    def set_current(self, channel: int, current_a: float) -> None:
        """Send `I<N> <NRF>` (Issue 14 printed p. 33)."""
        self.capabilities.validate_channel(channel)
        value = self.capabilities.validate_current(current_a)
        self._transport.command(f"I{channel} {value:.2f}")

    def read_set_voltage(self, channel: int) -> float:
        """Query `V<N>?` set voltage (Issue 14 printed p. 33)."""
        self.capabilities.validate_channel(channel)
        response = self._transport.query(f"V{channel}?")
        value = self._parse_prefixed_value(response, f"V{channel}")
        if value > self.capabilities.max_voltage_v:
            raise InstrumentProtocolError("Voltage setpoint response exceeds hardware capability.")
        return value

    def read_set_current(self, channel: int) -> float:
        """Query `I<N>?` set current (Issue 14 printed p. 33)."""
        self.capabilities.validate_channel(channel)
        response = self._transport.query(f"I{channel}?")
        value = self._parse_prefixed_value(response, f"I{channel}")
        if value > self.capabilities.max_current_a:
            raise InstrumentProtocolError("Current setpoint response exceeds hardware capability.")
        return value

    def output_on(self, channel: int) -> None:
        """Send `OP<N> 1` only when explicitly called (Issue 14 printed p. 33)."""
        self.capabilities.validate_channel(channel)
        self._transport.command(f"OP{channel} 1")

    def output_off(self, channel: int) -> None:
        """Send `OP<N> 0` only when explicitly called (Issue 14 printed p. 33)."""
        self.capabilities.validate_channel(channel)
        self._transport.command(f"OP{channel} 0")

    def confirm_operation_complete(self) -> None:
        """Require documented `*OPC?` response 1 (Issue 14 printed pp. 33-34)."""
        response = self._transport.query("*OPC?").strip()
        if response != "1":
            raise InstrumentProtocolError(
                "Malformed *OPC? response; expected documented value 1."
            )

    def read_output_state(self, channel: int) -> bool:
        """Query `OP<N>?` without changing output state (Issue 14 printed p. 33)."""
        self.capabilities.validate_channel(channel)
        response = self._transport.query(f"OP{channel}?").strip()
        if response not in {"0", "1"}:
            raise InstrumentProtocolError("Malformed output-state response; expected 0 or 1.")
        return response == "1"

    def read_voltage(self, channel: int) -> float:
        """Query `V<N>O?` measured voltage (Issue 14 printed p. 33)."""
        self.capabilities.validate_channel(channel)
        response = self._transport.query(f"V{channel}O?")
        return self._parse_suffixed_value(response, "V")

    def read_current(self, channel: int) -> float:
        """Query `I<N>O?` measured current (Issue 14 printed p. 33)."""
        self.capabilities.validate_channel(channel)
        response = self._transport.query(f"I{channel}O?")
        return self._parse_suffixed_value(response, "A")

    def read_voltage_range(self, channel: int) -> VoltageRangeCapability:
        """Query `VRANGE<N>?` (Issue 14 printed p. 34)."""
        self.capabilities.validate_channel(channel)
        response = self._transport.query(f"VRANGE{channel}?").strip()
        if not re.fullmatch(r"\d+", response):
            raise InstrumentProtocolError("Malformed voltage-range response; expected integer 1 or 2.")
        code = int(response)
        for voltage_range in self.capabilities.voltage_ranges:
            if voltage_range.remote_code == code:
                return voltage_range
        raise InstrumentProtocolError(f"Unsupported voltage-range response {code}.")

    def read_status_byte(self) -> int:
        """Query non-destructive `*STB?` (Issue 14 printed p. 35)."""
        response = self._transport.query("*STB?").strip()
        if not re.fullmatch(r"\d+", response):
            raise InstrumentProtocolError("Malformed status-byte response; expected an integer.")
        value = int(response)
        if not 0 <= value <= 255:
            raise InstrumentProtocolError("Status-byte response is outside 0-255.")
        return value

    @staticmethod
    def _parse_prefixed_value(response: str, prefix: str) -> float:
        match = re.fullmatch(rf"{re.escape(prefix)}\s+({_FIXED_NUMBER})", response.strip())
        if match is None:
            raise InstrumentProtocolError(f"Malformed {prefix} setpoint response.")
        return QPX600DPDriver._nonnegative_float(match.group(1))

    @staticmethod
    def _parse_suffixed_value(response: str, suffix: str) -> float:
        match = re.fullmatch(rf"({_FIXED_NUMBER}){re.escape(suffix)}", response.strip())
        if match is None:
            raise InstrumentProtocolError(f"Malformed measured-{suffix} response.")
        return QPX600DPDriver._nonnegative_float(match.group(1))

    @staticmethod
    def _nonnegative_float(text: str) -> float:
        try:
            value = float(text)
        except ValueError as error:
            raise InstrumentProtocolError("Response does not contain a valid fixed-point number.") from error
        if not math.isfinite(value) or value < 0:
            raise InstrumentProtocolError("Response value must be finite and nonnegative.")
        return value
