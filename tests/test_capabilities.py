"""Check the documented hardware contract and strict programming boundaries."""

from dataclasses import FrozenInstanceError, replace

import pytest

from cpx_electrolyser.instrument.capabilities import CPX400DP_CAPABILITIES
from cpx_electrolyser.instrument.exceptions import InstrumentValueError


def test_capabilities_match_supplied_manual() -> None:
    caps = CPX400DP_CAPABILITIES
    assert caps.number_of_channels == 2
    assert (caps.max_voltage_v, caps.max_current_a, caps.max_power_w) == (60.0, 20.0, 420.0)
    assert caps.voltage_programming_resolution_v == 0.01
    assert caps.current_programming_resolution_a == 0.001
    assert caps.measurement_voltage_resolution_v == 0.01
    assert caps.measurement_current_resolution_a == 0.01
    assert caps.supported_interfaces == ("USB virtual COM", "RS232", "LAN", "GPIB")


def test_capabilities_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        CPX400DP_CAPABILITIES.max_current_a = 999.0  # type: ignore[misc]
    with pytest.raises(InstrumentValueError):
        replace(CPX400DP_CAPABILITIES, supported_interfaces=["USB"])  # type: ignore[arg-type]


@pytest.mark.parametrize("field,value", [
    ("number_of_channels", True), ("number_of_channels", 0),
    ("max_voltage_v", float("nan")), ("max_current_a", -1),
    ("max_power_w", float("inf")), ("voltage_programming_resolution_v", 0),
    ("current_programming_resolution_a", 21), ("supported_interfaces", ()),
])
def test_invalid_capability_definitions_are_rejected(field: str, value: object) -> None:
    with pytest.raises(InstrumentValueError):
        replace(CPX400DP_CAPABILITIES, **{field: value})


def test_programming_grids_accept_endpoints_and_float_roundoff() -> None:
    caps = CPX400DP_CAPABILITIES
    assert caps.validate_voltage(0) == 0
    assert caps.validate_voltage(60) == 60
    assert caps.validate_current(0) == 0
    assert caps.validate_current(20) == 20
    assert caps.validate_voltage(0.1 + 0.2) == pytest.approx(0.3)
    assert caps.validate_current(0.001 + 0.002) == pytest.approx(0.003)
    with pytest.raises(InstrumentValueError, match="increments"):
        caps.validate_voltage(1e-12)  # Do not silently round a tiny request to zero.
