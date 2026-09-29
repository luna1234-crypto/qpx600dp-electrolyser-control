"""Atomic, backend-neutral channel state read-back."""

from dataclasses import dataclass

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface


@dataclass(frozen=True, slots=True)
class ChannelReadback:
    channel: int
    programmed_voltage_v: float
    current_limit_a: float
    measured_voltage_v: float
    measured_current_a: float
    voltage_range_name: str
    output_enabled: bool


def read_channel(supply: PowerSupplyInterface, channel: int) -> ChannelReadback:
    """Read one channel without allowing another queued action to interleave."""
    voltage_range = supply.read_voltage_range(channel)
    return ChannelReadback(
        channel=channel,
        programmed_voltage_v=supply.read_set_voltage(channel),
        current_limit_a=supply.read_set_current(channel),
        measured_voltage_v=supply.read_voltage(channel),
        measured_current_a=supply.read_current(channel),
        voltage_range_name=voltage_range.name,
        output_enabled=supply.read_output_state(channel),
    )
