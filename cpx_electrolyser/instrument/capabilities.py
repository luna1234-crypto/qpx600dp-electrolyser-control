"""Instrument limits grounded in the supplied CPX400DP Issue 1 manual."""

from dataclasses import dataclass
from typing import Final

from cpx_electrolyser.instrument._validation import finite_number, programming_value
from cpx_electrolyser.instrument.exceptions import InstrumentValueError


@dataclass(frozen=True, slots=True)
class InstrumentCapabilities:
    """Immutable per-output limits; power is an envelope bound, not a V/I rectangle.

    Measurement resolutions use the manual's meter specifications conservatively.
    No independent remote ADC cadence or extra measurement digits are promised.
    """

    number_of_channels: int
    max_voltage_v: float
    max_current_a: float
    max_power_w: float
    voltage_programming_resolution_v: float
    current_programming_resolution_a: float
    measurement_voltage_resolution_v: float
    measurement_current_resolution_a: float
    supported_interfaces: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.number_of_channels) is not int or self.number_of_channels < 1:
            raise InstrumentValueError("number_of_channels must be a positive integer.")
        for name in (
            "max_voltage_v", "max_current_a", "max_power_w",
            "voltage_programming_resolution_v", "current_programming_resolution_a",
            "measurement_voltage_resolution_v", "measurement_current_resolution_a",
        ):
            if finite_number(getattr(self, name), name) <= 0:
                raise InstrumentValueError(f"{name} must be positive.")
        if (
            type(self.supported_interfaces) is not tuple
            or not self.supported_interfaces
            or any(not isinstance(item, str) or not item.strip() for item in self.supported_interfaces)
        ):
            raise InstrumentValueError("supported_interfaces must be a nonempty tuple of names.")
        for resolution, maximum in (
            (self.voltage_programming_resolution_v, self.max_voltage_v),
            (self.current_programming_resolution_a, self.max_current_a),
            (self.measurement_voltage_resolution_v, self.max_voltage_v),
            (self.measurement_current_resolution_a, self.max_current_a),
        ):
            if resolution > maximum:
                raise InstrumentValueError("Resolution cannot exceed the corresponding full scale.")

    def validate_channel(self, channel: int) -> None:
        if type(channel) is not int or not 1 <= channel <= self.number_of_channels:
            raise InstrumentValueError(f"Channel must be an integer from 1 to {self.number_of_channels}.")

    def validate_voltage(self, voltage_v: float) -> float:
        return programming_value(
            voltage_v, "voltage_v", self.max_voltage_v, self.voltage_programming_resolution_v
        )

    def validate_current(self, current_a: float) -> float:
        return programming_value(
            current_a, "current_a", self.max_current_a, self.current_programming_resolution_a
        )


# Immutable shared facts, not mutable instrument state. Printed manual pp.2-4.
CPX400DP_CAPABILITIES: Final = InstrumentCapabilities(
    number_of_channels=2,
    max_voltage_v=60.0,
    max_current_a=20.0,
    max_power_w=420.0,
    voltage_programming_resolution_v=0.01,
    current_programming_resolution_a=0.001,
    measurement_voltage_resolution_v=0.01,
    measurement_current_resolution_a=0.01,
    supported_interfaces=("USB virtual COM", "RS232", "LAN", "GPIB"),
)
