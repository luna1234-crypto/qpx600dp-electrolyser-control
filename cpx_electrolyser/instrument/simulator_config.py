"""Immutable, independently configurable simulated electrolyser loads."""

from dataclasses import dataclass, field
import math

from cpx_electrolyser.instrument._validation import finite_number
from cpx_electrolyser.instrument.capabilities import CPX400DP_CAPABILITIES
from cpx_electrolyser.instrument.exceptions import InstrumentValueError


@dataclass(frozen=True, slots=True)
class ElectrolyserModel:
    """Toy load: V = V0 + R*I + a*ln(1 + I/1 A), for positive current.

    The logarithm's reference current is 1 A. R must be positive so the inverse
    is unique. This model does not represent fitted scientific measurements.
    """

    onset_voltage_v: float = 1.5
    resistance_ohm: float = 0.1
    logarithmic_coefficient_v: float = 0.1

    def __post_init__(self) -> None:
        for name in ("onset_voltage_v", "resistance_ohm", "logarithmic_coefficient_v"):
            value = finite_number(getattr(self, name), name)
            if value < 0 or (name == "resistance_ohm" and value == 0):
                raise InstrumentValueError(f"{name} is outside the supported model range.")
        if not math.isfinite(self.voltage_at_current(CPX400DP_CAPABILITIES.max_current_a)):
            raise InstrumentValueError("Load model overflows within the instrument current range.")

    def voltage_at_current(self, current_a: float) -> float:
        """Evaluate the load curve; the caller supplies a nonnegative current."""
        return (
            self.onset_voltage_v
            + self.resistance_ohm * current_a
            + self.logarithmic_coefficient_v * math.log1p(current_a)
        )


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Simulation settings only, not persistent application configuration."""

    channel_models: tuple[ElectrolyserModel, ElectrolyserModel] = field(
        default_factory=lambda: (ElectrolyserModel(), ElectrolyserModel())
    )
    communication_delay_s: float = 0.01
    voltage_noise_sd_v: float = 0.0
    current_noise_sd_a: float = 0.0
    random_seed: int | None = 0
    max_modelled_power_w: float = 400.0

    def __post_init__(self) -> None:
        if (
            type(self.channel_models) is not tuple
            or len(self.channel_models) != CPX400DP_CAPABILITIES.number_of_channels
            or any(not isinstance(model, ElectrolyserModel) for model in self.channel_models)
        ):
            raise InstrumentValueError("Supply exactly two immutable ElectrolyserModel instances.")
        for name, maximum in (
            ("communication_delay_s", 5.0),
            ("voltage_noise_sd_v", CPX400DP_CAPABILITIES.max_voltage_v),
            ("current_noise_sd_a", CPX400DP_CAPABILITIES.max_current_a),
            ("max_modelled_power_w", 400.0),
        ):
            value = finite_number(getattr(self, name), name)
            if not 0 <= value <= maximum or (name == "max_modelled_power_w" and value == 0):
                raise InstrumentValueError(f"{name} is outside its supported simulation range.")
        if self.random_seed is not None and type(self.random_seed) is not int:
            raise InstrumentValueError("random_seed must be an integer or None.")
