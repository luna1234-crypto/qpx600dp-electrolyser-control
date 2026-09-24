"""Check the QPX600DP hardware contract and conservative documented envelope."""

from dataclasses import FrozenInstanceError, replace

import pytest

from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES
from qpx_electrolyser.instrument.exceptions import InstrumentValueError


def test_capabilities_match_issue14_manual() -> None:
    caps = QPX600DP_CAPABILITIES
    assert caps.number_of_channels == 2
    assert (caps.max_voltage_v, caps.min_current_a, caps.max_current_a, caps.max_power_w) == (
        80.0, 0.01, 50.0, 600.0
    )
    assert [
        (
            item.name,
            item.remote_code,
            item.max_voltage_v,
            item.programming_resolution_v,
            item.measurement_resolution_v,
        )
        for item in caps.voltage_ranges
    ] == [
        ("60 V", 1, 60.0, 0.001, 0.001),
        ("80 V", 2, 80.0, 0.002, 0.002),
    ]
    assert caps.current_programming_resolution_a == 0.01
    assert caps.measurement_current_resolution_a == 0.01
    assert caps.supported_interfaces == ("USB virtual COM", "RS232", "LAN", "analogue")
    assert caps.optional_interfaces == ("GPIB",)


def test_capabilities_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        QPX600DP_CAPABILITIES.max_current_a = 999.0  # type: ignore[misc]
    with pytest.raises(InstrumentValueError):
        replace(QPX600DP_CAPABILITIES, supported_interfaces=["USB"])  # type: ignore[arg-type]


@pytest.mark.parametrize("field,value", [
    ("number_of_channels", True), ("number_of_channels", 0),
    ("voltage_ranges", []), ("min_current_a", 0),
    ("max_current_a", -1), ("max_power_w", float("inf")),
    ("current_programming_resolution_a", 0), ("supported_interfaces", ()),
])
def test_invalid_capability_definitions_are_rejected(field: str, value: object) -> None:
    with pytest.raises(InstrumentValueError):
        replace(QPX600DP_CAPABILITIES, **{field: value})


def test_range_specific_programming_grids_and_current_minimum() -> None:
    caps = QPX600DP_CAPABILITIES
    assert caps.validate_voltage(0, "60 V") == 0
    assert caps.validate_voltage(60, "60 V") == 60
    assert caps.validate_voltage(80, "80 V") == 80
    assert caps.validate_voltage(0.1 + 0.2, "60 V") == pytest.approx(0.3)
    assert caps.validate_voltage(0.002, "80 V") == 0.002
    assert caps.validate_current(0.01) == 0.01
    assert caps.validate_current(50) == 50
    with pytest.raises(InstrumentValueError, match="increments"):
        caps.validate_voltage(0.001, "80 V")
    with pytest.raises(InstrumentValueError, match="between"):
        caps.validate_voltage(60.001, "60 V")
    with pytest.raises(InstrumentValueError, match="between"):
        caps.validate_current(0)
    with pytest.raises(InstrumentValueError, match="Unknown voltage range"):
        caps.validate_voltage(1, "unknown")


def test_power_envelope_uses_documented_anchors_without_interpolation() -> None:
    envelope = QPX600DP_CAPABILITIES.power_envelope
    assert envelope.max_current_at_voltage(0) == 50
    assert envelope.max_current_at_voltage(10) == 50
    assert envelope.max_current_at_voltage(10.001) == 7.5
    assert envelope.max_current_at_voltage(80) == 7.5
    assert envelope.contains(10, 50)
    assert envelope.contains(80, 7.5)
    assert envelope.contains(20, 7.5)
    assert not envelope.contains(20, 7.51)
    assert not envelope.contains(80, 7.51)
