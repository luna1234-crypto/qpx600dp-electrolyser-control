"""Documented QPX600DP hardware capabilities, separate from software safety policy."""

from dataclasses import dataclass
from typing import Final

from qpx_electrolyser.instrument._validation import finite_number, programming_value
from qpx_electrolyser.instrument.exceptions import InstrumentValueError


@dataclass(frozen=True, slots=True)
class VoltageRangeCapability:
    """One selectable QPX600DP voltage range (Issue 14, printed pp. 2, 11, 33)."""

    name: str
    remote_code: int
    max_voltage_v: float
    programming_resolution_v: float
    measurement_resolution_v: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InstrumentValueError("Voltage range name must be nonempty.")
        if type(self.remote_code) is not int or self.remote_code < 1:
            raise InstrumentValueError("Voltage range remote_code must be a positive integer.")
        for field_name in (
            "max_voltage_v", "programming_resolution_v", "measurement_resolution_v"
        ):
            if finite_number(getattr(self, field_name), field_name) <= 0:
                raise InstrumentValueError(f"{field_name} must be positive.")
        if self.programming_resolution_v > self.max_voltage_v:
            raise InstrumentValueError("Programming resolution cannot exceed voltage full scale.")
        if self.measurement_resolution_v > self.max_voltage_v:
            raise InstrumentValueError("Measurement resolution cannot exceed voltage full scale.")


@dataclass(frozen=True, slots=True)
class ConservativePowerEnvelope:
    """Restrictive segments using only documented Issue 14 anchor points.

    The manual gives 10 V / 50 A and 80 V / 7.5 A guaranteed points and says
    lower voltages are current-limited to 50 A, but does not state a complete
    mathematical curve. The unknown 10-80 V region is therefore not
    interpolated: this model permits at most 7.5 A above 10 V. It may reject
    operating points that real hardware supports, but never claims an invented
    curve as a rating.
    """

    low_voltage_boundary_v: float
    low_voltage_max_current_a: float
    high_voltage_max_current_a: float
    max_voltage_v: float
    max_power_w: float

    def __post_init__(self) -> None:
        for field_name in (
            "low_voltage_boundary_v", "low_voltage_max_current_a",
            "high_voltage_max_current_a", "max_voltage_v", "max_power_w",
        ):
            if finite_number(getattr(self, field_name), field_name) <= 0:
                raise InstrumentValueError(f"{field_name} must be positive.")
        if self.low_voltage_boundary_v >= self.max_voltage_v:
            raise InstrumentValueError("Power-envelope voltage boundaries are out of order.")
        if self.high_voltage_max_current_a > self.low_voltage_max_current_a:
            raise InstrumentValueError("High-voltage envelope current must not exceed low-voltage current.")

    def max_current_at_voltage(self, voltage_v: float) -> float:
        voltage = finite_number(voltage_v, "voltage_v")
        if not 0 <= voltage <= self.max_voltage_v:
            raise InstrumentValueError(
                f"voltage_v must be between 0 and {self.max_voltage_v:g}."
            )
        if voltage <= self.low_voltage_boundary_v:
            return self.low_voltage_max_current_a
        return self.high_voltage_max_current_a

    def contains(self, voltage_v: float, current_a: float) -> bool:
        voltage = finite_number(voltage_v, "voltage_v")
        current = finite_number(current_a, "current_a")
        if voltage < 0 or current < 0 or voltage > self.max_voltage_v:
            return False
        tolerance = 1e-12
        return (
            current <= self.max_current_at_voltage(voltage) + tolerance
            and voltage * current <= self.max_power_w + tolerance
        )


@dataclass(frozen=True, slots=True)
class InstrumentCapabilities:
    """Immutable hardware ratings; these are not application safety limits."""

    number_of_channels: int
    voltage_ranges: tuple[VoltageRangeCapability, ...]
    min_current_a: float
    max_current_a: float
    max_power_w: float
    current_programming_resolution_a: float
    measurement_current_resolution_a: float
    supported_interfaces: tuple[str, ...]
    optional_interfaces: tuple[str, ...]
    power_envelope: ConservativePowerEnvelope

    def __post_init__(self) -> None:
        if type(self.number_of_channels) is not int or self.number_of_channels < 1:
            raise InstrumentValueError("number_of_channels must be a positive integer.")
        if (
            type(self.voltage_ranges) is not tuple
            or not self.voltage_ranges
            or any(not isinstance(item, VoltageRangeCapability) for item in self.voltage_ranges)
        ):
            raise InstrumentValueError("voltage_ranges must be a nonempty tuple of ranges.")
        if len({item.name for item in self.voltage_ranges}) != len(self.voltage_ranges):
            raise InstrumentValueError("Voltage range names must be unique.")
        if len({item.remote_code for item in self.voltage_ranges}) != len(self.voltage_ranges):
            raise InstrumentValueError("Voltage range remote codes must be unique.")
        for field_name in (
            "min_current_a", "max_current_a", "max_power_w",
            "current_programming_resolution_a", "measurement_current_resolution_a",
        ):
            if finite_number(getattr(self, field_name), field_name) <= 0:
                raise InstrumentValueError(f"{field_name} must be positive.")
        if self.min_current_a > self.max_current_a:
            raise InstrumentValueError("min_current_a cannot exceed max_current_a.")
        for names, label in (
            (self.supported_interfaces, "supported_interfaces"),
            (self.optional_interfaces, "optional_interfaces"),
        ):
            if (
                type(names) is not tuple
                or any(not isinstance(item, str) or not item.strip() for item in names)
            ):
                raise InstrumentValueError(f"{label} must be a tuple of nonempty names.")
        if not self.supported_interfaces:
            raise InstrumentValueError("supported_interfaces must not be empty.")
        if not isinstance(self.power_envelope, ConservativePowerEnvelope):
            raise InstrumentValueError("power_envelope must be a ConservativePowerEnvelope.")
        if self.power_envelope.max_voltage_v != self.max_voltage_v:
            raise InstrumentValueError("Power envelope and voltage ranges must share the same maximum.")
        if self.power_envelope.low_voltage_max_current_a != self.max_current_a:
            raise InstrumentValueError("Power envelope and current rating must share the same maximum.")
        if self.power_envelope.max_power_w != self.max_power_w:
            raise InstrumentValueError("Power envelope and power rating must share the same maximum.")

    @property
    def max_voltage_v(self) -> float:
        return max(item.max_voltage_v for item in self.voltage_ranges)

    def voltage_range(self, name: str) -> VoltageRangeCapability:
        for voltage_range in self.voltage_ranges:
            if voltage_range.name == name:
                return voltage_range
        choices = ", ".join(item.name for item in self.voltage_ranges)
        raise InstrumentValueError(f"Unknown voltage range {name!r}; choose one of: {choices}.")

    def validate_channel(self, channel: int) -> None:
        if type(channel) is not int or not 1 <= channel <= self.number_of_channels:
            raise InstrumentValueError(f"Channel must be an integer from 1 to {self.number_of_channels}.")

    def validate_voltage(self, voltage_v: float, voltage_range_name: str) -> float:
        voltage_range = self.voltage_range(voltage_range_name)
        return programming_value(
            voltage_v,
            "voltage_v",
            voltage_range.max_voltage_v,
            voltage_range.programming_resolution_v,
        )

    def validate_current(self, current_a: float) -> float:
        return programming_value(
            current_a,
            "current_a",
            self.max_current_a,
            self.current_programming_resolution_a,
            minimum=self.min_current_a,
        )


# Issue 14 printed pp. 2-4, 11 and 13. The high-range measurement value is
# conservatively 2 mV because printed p. 11 says both set and actual values use
# 2 mV there, despite the generic 1 mV meter-resolution line on printed p. 3.
QPX600DP_CAPABILITIES: Final = InstrumentCapabilities(
    number_of_channels=2,
    voltage_ranges=(
        VoltageRangeCapability("60 V", 1, 60.0, 0.001, 0.001),
        VoltageRangeCapability("80 V", 2, 80.0, 0.002, 0.002),
    ),
    min_current_a=0.01,
    max_current_a=50.0,
    max_power_w=600.0,
    current_programming_resolution_a=0.01,
    measurement_current_resolution_a=0.01,
    supported_interfaces=("USB virtual COM", "RS232", "LAN", "analogue"),
    optional_interfaces=("GPIB",),
    power_envelope=ConservativePowerEnvelope(
        low_voltage_boundary_v=10.0,
        low_voltage_max_current_a=50.0,
        high_voltage_max_current_a=7.5,
        max_voltage_v=80.0,
        max_power_w=600.0,
    ),
)
