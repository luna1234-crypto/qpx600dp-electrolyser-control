"""Simulator acceptance: real readback behaviour, independent state and no hardware."""

from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError

import pytest

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.exceptions import (
    InstrumentNotConnectedError,
    InstrumentValueError,
    UnsupportedOperatingPointError,
)
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import (
    ElectrolyserModel,
    SimulatorConfig,
    UserSafetyLimits,
)


@pytest.fixture
def supply() -> Iterator[PowerSupplyInterface]:
    backend: PowerSupplyInterface = SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0))
    backend.connect()
    try:
        yield backend
    finally:
        if backend.is_connected():
            backend.output_off(1)
            backend.output_off(2)
            backend.disconnect()


def snapshot(supply: PowerSupplyInterface, channel: int) -> tuple[float, float, bool, float, float]:
    return (
        supply.read_set_voltage(channel), supply.read_set_current(channel),
        supply.read_output_state(channel), supply.read_voltage(channel), supply.read_current(channel),
    )


def configure_cc(supply: PowerSupplyInterface, channel: int, current_a: float) -> None:
    supply.set_voltage(channel, 5.0)
    supply.set_current(channel, current_a)
    supply.output_on(channel)


def test_interface_is_abstract_and_simulator_identifies_itself(supply: PowerSupplyInterface) -> None:
    with pytest.raises(TypeError):
        PowerSupplyInterface()  # type: ignore[abstract]
    assert isinstance(supply, PowerSupplyInterface)
    manufacturer, model, serial, version = supply.identify().split(",")
    assert (manufacturer, model, serial) == ("SIMULATOR", "QPX600DP", "SIMULATED")
    assert version


@pytest.mark.parametrize("operation", [
    lambda s: s.identify(), lambda s: s.set_voltage(1, 2), lambda s: s.set_current(1, 1),
    lambda s: s.read_set_voltage(1), lambda s: s.read_set_current(1),
    lambda s: s.read_voltage_range(1),
    lambda s: s.output_on(1), lambda s: s.output_off(1),
    lambda s: s.read_output_state(1), lambda s: s.read_voltage(1), lambda s: s.read_current(1),
])
def test_disconnected_operations_raise(operation: Callable[[PowerSupplyInterface], object]) -> None:
    backend = SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0))
    assert not backend.is_connected()
    assert backend.capabilities.number_of_channels == 2
    with pytest.raises(InstrumentNotConnectedError):
        operation(backend)


@pytest.mark.parametrize("channel", [1, 2])
def test_setpoints_do_not_energise_output_or_pretend_to_be_measurements(
    supply: PowerSupplyInterface, channel: int,
) -> None:
    assert snapshot(supply, channel) == (0, 0, False, 0, 0)
    supply.set_voltage(channel, 3.0)
    supply.set_current(channel, 0.12)
    assert snapshot(supply, channel) == pytest.approx((3.0, 0.12, False, 0.0, 0.0))
    supply.output_on(channel)
    assert supply.read_set_current(channel) == pytest.approx(0.12)
    assert supply.read_current(channel) == pytest.approx(0.12)  # 10 mA meter resolution
    assert supply.read_voltage(channel) < supply.read_set_voltage(channel)
    supply.output_off(channel)
    assert snapshot(supply, channel) == pytest.approx((3.0, 0.12, False, 0.0, 0.0))


@pytest.mark.parametrize("current_channel,other_channel", [(1, 2), (2, 1)])
def test_channels_start_change_and_stop_independently(
    supply: PowerSupplyInterface, current_channel: int, other_channel: int,
) -> None:
    configure_cc(supply, current_channel, 10.0)
    assert supply.read_current(current_channel) == 10.0
    assert snapshot(supply, other_channel) == (0, 0, False, 0, 0)
    current_before = snapshot(supply, current_channel)
    supply.set_voltage(other_channel, 2.2)
    supply.set_current(other_channel, 20.0)
    supply.output_on(other_channel)
    assert supply.read_voltage(other_channel) == pytest.approx(2.2)
    assert 0 < supply.read_current(other_channel) < 20
    assert snapshot(supply, current_channel) == current_before
    other_before = snapshot(supply, other_channel)
    supply.set_current(current_channel, 5.0)
    supply.output_off(current_channel)
    assert snapshot(supply, other_channel) == other_before
    supply.output_on(current_channel)
    assert supply.read_current(current_channel) == 5.0
    supply.output_off(other_channel)
    assert supply.read_current(current_channel) == 5.0
    assert supply.read_output_state(current_channel)


def test_cc_readback_uses_nonlinear_cell_curve(supply: PowerSupplyInterface) -> None:
    configure_cc(supply, 1, 10.0)
    # V = 1.5 + 0.1*10 + 0.1*ln(11) = 2.7397895... V, displayed at 10 mV.
    assert supply.read_voltage(1) == pytest.approx(2.74)
    assert supply.read_current(1) == pytest.approx(10.0)


def test_cv_and_cc_crossover_with_known_linear_load() -> None:
    model = ElectrolyserModel(resistance_ohm=1.0, logarithmic_coefficient_v=0.0)
    backend = SimulatedPowerSupply(SimulatorConfig(channel_models=(model, model), communication_delay_s=0))
    backend.connect()
    try:
        backend.set_voltage(1, 2.5)
        backend.set_current(1, 2.0)
        backend.output_on(1)
        assert (backend.read_voltage(1), backend.read_current(1)) == (2.5, 1.0)
        backend.set_current(1, 0.5)
        assert (backend.read_voltage(1), backend.read_current(1)) == (2.0, 0.5)
        backend.set_voltage(1, 1.75)  # Compliance now prevents reaching 0.5 A.
        assert (backend.read_voltage(1), backend.read_current(1)) == (1.75, 0.25)
        backend.set_voltage(1, 1.0)  # Below the 1.5 V onset: applied V, no current.
        assert (backend.read_voltage(1), backend.read_current(1)) == (1.0, 0.0)
        backend.set_voltage(1, 0.0)
        assert (backend.read_voltage(1), backend.read_current(1)) == (0.0, 0.0)
        before = snapshot(backend, 1)
        with pytest.raises(InstrumentValueError, match="between"):
            backend.set_current(1, 0.0)
        assert snapshot(backend, 1) == before
    finally:
        backend.output_off(1)
        backend.disconnect()


def test_independently_configurable_load_models() -> None:
    first = ElectrolyserModel(resistance_ohm=0.1, logarithmic_coefficient_v=0)
    second = ElectrolyserModel(resistance_ohm=0.2, logarithmic_coefficient_v=0)
    backend = SimulatedPowerSupply(SimulatorConfig(channel_models=(first, second), communication_delay_s=0))
    backend.connect()
    try:
        configure_cc(backend, 1, 5)
        configure_cc(backend, 2, 5)
        assert backend.read_voltage(1) == 2.0
        assert backend.read_voltage(2) == 2.5
    finally:
        backend.output_off(1)
        backend.output_off(2)
        backend.disconnect()


@pytest.mark.parametrize("channel", [0, 3, -1, True, 1.0, "1", None])
def test_invalid_channel_never_aliases_a_real_channel(supply: PowerSupplyInterface, channel: object) -> None:
    before = (snapshot(supply, 1), snapshot(supply, 2))
    operations = (
        lambda: supply.set_voltage(channel, 2), lambda: supply.set_current(channel, 1),
        lambda: supply.output_on(channel), lambda: supply.output_off(channel),
        lambda: supply.read_voltage(channel), lambda: supply.read_current(channel),
        lambda: supply.read_set_voltage(channel), lambda: supply.read_set_current(channel),
        lambda: supply.read_output_state(channel),
    )
    for operation in operations:
        with pytest.raises(InstrumentValueError):
            operation()
    assert (snapshot(supply, 1), snapshot(supply, 2)) == before


@pytest.mark.parametrize("quantity,value", [
    ("voltage", -0.001), ("voltage", 60.001), ("voltage", 2.0001),
    ("current", 0), ("current", 50.01), ("current", 1.001),
    ("voltage", float("nan")), ("voltage", float("inf")),
    ("current", float("-inf")), ("current", True),
    ("voltage", "2.0"), ("current", None),
])
def test_rejected_setpoints_leave_active_channels_unchanged(
    supply: PowerSupplyInterface, quantity: str, value: object,
) -> None:
    configure_cc(supply, 1, 2)
    configure_cc(supply, 2, 3)
    before = (snapshot(supply, 1), snapshot(supply, 2))
    setter = supply.set_voltage if quantity == "voltage" else supply.set_current
    with pytest.raises(InstrumentValueError):
        setter(1, value)  # type: ignore[arg-type]
    assert (snapshot(supply, 1), snapshot(supply, 2)) == before


def test_unsupported_power_fails_without_applying_on_or_setpoint() -> None:
    model = ElectrolyserModel(onset_voltage_v=0, resistance_ohm=3, logarithmic_coefficient_v=0)
    backend = SimulatedPowerSupply(SimulatorConfig(channel_models=(model, model), communication_delay_s=0))
    backend.connect()
    try:
        backend.set_voltage(1, 60)
        backend.set_current(1, 20)
        with pytest.raises(UnsupportedOperatingPointError, match="not applied"):
            backend.output_on(1)  # Ideal model would demand 1200 W.
        assert not backend.read_output_state(1)
        backend.set_current(1, 7)  # 21 V * 7 A is inside the conservative segment.
        backend.output_on(1)
        before = snapshot(backend, 1)
        with pytest.raises(UnsupportedOperatingPointError):
            backend.set_current(1, 8)  # 24 V * 8 A is above the conservative 7.5 A segment.
        assert snapshot(backend, 1) == before
        assert not backend.read_output_state(2)
    finally:
        backend.output_off(1)
        backend.disconnect()


def test_documented_power_envelope_anchor_points_are_supported() -> None:
    low_range_model = ElectrolyserModel(
        onset_voltage_v=0, resistance_ohm=0.2, logarithmic_coefficient_v=0
    )
    high_range_model = ElectrolyserModel(
        onset_voltage_v=5, resistance_ohm=10, logarithmic_coefficient_v=0
    )
    backend = SimulatedPowerSupply(SimulatorConfig(
        channel_models=(low_range_model, high_range_model),
        channel_voltage_ranges=("60 V", "80 V"),
        communication_delay_s=0,
    ))
    backend.connect()
    try:
        backend.set_voltage(1, 10)
        backend.set_current(1, 50)
        backend.output_on(1)
        assert (backend.read_voltage(1), backend.read_current(1)) == (10, 50)

        backend.set_voltage(2, 80)
        backend.set_current(2, 7.5)
        backend.output_on(2)
        assert (backend.read_voltage(2), backend.read_current(2)) == (80, 7.5)
    finally:
        backend.output_off(1)
        backend.output_off(2)
        backend.disconnect()


def test_voltage_range_controls_programming_and_measurement_resolution() -> None:
    model = ElectrolyserModel(
        onset_voltage_v=0.003, resistance_ohm=0.2, logarithmic_coefficient_v=0
    )
    backend = SimulatedPowerSupply(SimulatorConfig(
        channel_models=(model, model),
        channel_voltage_ranges=("60 V", "80 V"),
        communication_delay_s=0,
    ))
    backend.connect()
    try:
        with pytest.raises(InstrumentValueError):
            backend.set_voltage(1, 60.001)
        with pytest.raises(InstrumentValueError, match="increments"):
            backend.set_voltage(2, 79.999)
        for channel in (1, 2):
            backend.set_voltage(channel, 5)
            backend.set_current(channel, 10)
            backend.output_on(channel)
        assert backend.read_voltage(1) == pytest.approx(2.003)
        high_range_readback = backend.read_voltage(2)
        assert high_range_readback / 0.002 == pytest.approx(round(high_range_readback / 0.002))
    finally:
        backend.output_off(1)
        backend.output_off(2)
        backend.disconnect()


def test_user_safety_limits_are_explicit_and_separate_from_hardware() -> None:
    assert SimulatorConfig().user_safety_limits is None
    limits = UserSafetyLimits(max_voltage_v=5, max_current_a=5, max_power_w=10)
    backend = SimulatedPowerSupply(SimulatorConfig(
        user_safety_limits=limits, communication_delay_s=0
    ))
    backend.connect()
    try:
        with pytest.raises(UnsupportedOperatingPointError, match="Voltage setpoint"):
            backend.set_voltage(1, 5.001)
        backend.set_voltage(1, 5)
        with pytest.raises(UnsupportedOperatingPointError, match="Current setpoint"):
            backend.set_current(1, 5.01)
        backend.set_current(1, 5)
        with pytest.raises(UnsupportedOperatingPointError, match="user safety"):
            backend.output_on(1)
        assert not backend.read_output_state(1)
        backend.set_current(1, 4)
        backend.output_on(1)
        assert backend.read_output_state(1)
    finally:
        backend.output_off(1)
        backend.disconnect()


@pytest.mark.parametrize("limits", [
    {"max_voltage_v": 0, "max_current_a": 1, "max_power_w": 1},
    {"max_voltage_v": 81, "max_current_a": 1, "max_power_w": 1},
    {"max_voltage_v": 1, "max_current_a": 51, "max_power_w": 1},
    {"max_voltage_v": 1, "max_current_a": 1, "max_power_w": 601},
])
def test_invalid_user_safety_limits_are_rejected(limits: dict[str, float]) -> None:
    with pytest.raises(InstrumentValueError):
        UserSafetyLimits(**limits)


def test_connection_lifecycle_does_not_hide_energised_outputs(supply: PowerSupplyInterface) -> None:
    configure_cc(supply, 1, 1)
    configure_cc(supply, 2, 2)
    before = (snapshot(supply, 1), snapshot(supply, 2))
    supply.connect()  # Idempotent, not a reset.
    assert (snapshot(supply, 1), snapshot(supply, 2)) == before
    supply.disconnect()
    supply.disconnect()
    assert not supply.is_connected()
    with pytest.raises(InstrumentNotConnectedError):
        supply.read_output_state(1)
    supply.connect()
    assert (snapshot(supply, 1), snapshot(supply, 2)) == before


def test_latency_is_applied_once_per_command_without_sleeping_in_tests() -> None:
    waits: list[float] = []
    backend = SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0.025), delay=waits.append)
    backend.connect()
    backend.connect()  # Local no-op.
    assert backend.is_connected()
    assert backend.capabilities.number_of_channels == 2
    backend.identify()
    backend.set_voltage(1, 2)
    backend.set_current(1, 1)
    backend.output_on(1)
    backend.read_voltage(1)
    backend.read_current(1)
    backend.read_set_voltage(1)
    backend.read_set_current(1)
    backend.read_voltage_range(1)
    backend.read_output_state(1)
    with pytest.raises(InstrumentValueError):
        backend.set_voltage(1, 2.0001)  # Rejected before simulated I/O.
    backend.output_off(1)
    backend.disconnect()
    backend.disconnect()  # Local no-op.
    assert waits == [0.025] * 13


def test_noise_is_reproducible_and_independent_of_sibling_reads() -> None:
    config = SimulatorConfig(
        communication_delay_s=0, voltage_noise_sd_v=0.05, current_noise_sd_a=0.05, random_seed=123,
    )
    baseline, interleaved = SimulatedPowerSupply(config), SimulatedPowerSupply(config)
    samples: list[tuple[float, float]] = []
    for backend in (baseline, interleaved):
        backend.connect()
        configure_cc(backend, 1, 5)
        configure_cc(backend, 2, 5)
    try:
        for _ in range(30):
            interleaved.read_voltage(1)
            interleaved.read_current(1)
            expected = (baseline.read_voltage(2), baseline.read_current(2))
            actual = (interleaved.read_voltage(2), interleaved.read_current(2))
            assert actual == expected
            samples.append(actual)
        assert len(set(samples)) > 1
        for voltage_v, current_a in samples:
            assert voltage_v / 0.001 == pytest.approx(round(voltage_v / 0.001))
            assert current_a / 0.01 == pytest.approx(round(current_a / 0.01))
        baseline.output_off(1)
        assert baseline.read_voltage(1) == 0 and baseline.read_current(1) == 0
        assert interleaved.read_output_state(1)  # Instances have no shared mutable state.
    finally:
        for backend in (baseline, interleaved):
            backend.output_off(1)
            backend.output_off(2)
            backend.disconnect()


@pytest.mark.parametrize("settings", [
    {"communication_delay_s": -1}, {"communication_delay_s": 6},
    {"voltage_noise_sd_v": float("nan")}, {"current_noise_sd_a": -0.01},
    {"channel_voltage_ranges": ("60 V", "invalid")},
    {"channel_voltage_ranges": ("60 V",)}, {"user_safety_limits": object()},
    {"random_seed": True}, {"channel_models": ()},
])
def test_invalid_simulation_configuration_is_rejected(settings: dict[str, object]) -> None:
    with pytest.raises(InstrumentValueError):
        SimulatorConfig(**settings)  # type: ignore[arg-type]


@pytest.mark.parametrize("settings", [
    {"resistance_ohm": 0}, {"resistance_ohm": -1}, {"resistance_ohm": 1e308},
    {"onset_voltage_v": -1}, {"logarithmic_coefficient_v": -0.1},
    {"onset_voltage_v": float("nan")},
])
def test_invalid_cell_model_is_rejected(settings: dict[str, float]) -> None:
    with pytest.raises(InstrumentValueError):
        ElectrolyserModel(**settings)


def test_configuration_cannot_be_mutated_while_outputs_are_running() -> None:
    backend = SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0))
    with pytest.raises(FrozenInstanceError):
        backend.config.communication_delay_s = 5  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        backend.config.channel_models[0].resistance_ohm = 20  # type: ignore[misc]
