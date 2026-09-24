"""Immutable simulated loads, voltage ranges and optional user safety limits."""

from dataclasses import dataclass, field
import math

from qpx_electrolyser.instrument._validation import finite_number
from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES
from qpx_electrolyser.instrument.exceptions import InstrumentValueError


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
        if not math.isfinite(self.voltage_at_current(QPX600DP_CAPABILITIES.max_current_a)):
            raise InstrumentValueError("Load model overflows within the instrument current range.")

    def voltage_at_current(self, current_a: float) -> float:
        """Evaluate the load curve; the caller supplies a nonnegative current."""
        return (
            self.onset_voltage_v
            + self.resistance_ohm * current_a
            + self.logarithmic_coefficient_v * math.log1p(current_a)
        )


@dataclass(frozen=True, slots=True)
class UserSafetyLimits:
    """Optional operator-configured limits, independent of hardware ratings.

    No instance is supplied by default. Future experiment configuration must
    explicitly create these limits instead of treating 80 V, 50 A or 600 W as
    safe experimental defaults.
    """

    max_voltage_v: float
    max_current_a: float
    max_power_w: float

    def __post_init__(self) -> None:
        hardware = QPX600DP_CAPABILITIES
        for name, maximum in (
            ("max_voltage_v", hardware.max_voltage_v),
            ("max_current_a", hardware.max_current_a),
            ("max_power_w", hardware.max_power_w),
        ):
            value = finite_number(getattr(self, name), name)
            if not 0 < value <= maximum:
                raise InstrumentValueError(
                    f"{name} must be positive and no greater than the hardware rating {maximum:g}."
                )


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Simulation settings only, not persistent application configuration."""

    channel_models: tuple[ElectrolyserModel, ElectrolyserModel] = field(
        default_factory=lambda: (ElectrolyserModel(), ElectrolyserModel())
    )
    channel_voltage_ranges: tuple[str, str] = ("60 V", "60 V")
    user_safety_limits: UserSafetyLimits | None = None
    communication_delay_s: float = 0.01
    voltage_noise_sd_v: float = 0.0
    current_noise_sd_a: float = 0.0
    random_seed: int | None = 0

    def __post_init__(self) -> None:
        hardware = QPX600DP_CAPABILITIES
        if (
            type(self.channel_models) is not tuple
            or len(self.channel_models) != hardware.number_of_channels
            or any(not isinstance(model, ElectrolyserModel) for model in self.channel_models)
        ):
            raise InstrumentValueError("Supply exactly two immutable ElectrolyserModel instances.")
        if (
            type(self.channel_voltage_ranges) is not tuple
            or len(self.channel_voltage_ranges) != hardware.number_of_channels
        ):
            raise InstrumentValueError("Supply exactly two voltage-range names.")
        for name in self.channel_voltage_ranges:
            hardware.voltage_range(name)
        if self.user_safety_limits is not None and not isinstance(
            self.user_safety_limits, UserSafetyLimits
        ):
            raise InstrumentValueError("user_safety_limits must be UserSafetyLimits or None.")
        for name, maximum in (
            ("communication_delay_s", 5.0),
            ("voltage_noise_sd_v", hardware.max_voltage_v),
            ("current_noise_sd_a", hardware.max_current_a),
        ):
            value = finite_number(getattr(self, name), name)
            if not 0 <= value <= maximum:
                raise InstrumentValueError(f"{name} is outside its supported simulation range.")
        if self.random_seed is not None and type(self.random_seed) is not int:
            raise InstrumentValueError("random_seed must be an integer or None.")
