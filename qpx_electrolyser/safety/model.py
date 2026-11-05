"""One typed M16 safety policy shared by manual and protocol controllers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from threading import RLock

from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES
from qpx_electrolyser.instrument.exceptions import InstrumentValueError


class SafetyValidationError(ValueError):
    """A requested operating point exceeds the effective safety policy."""


@dataclass(frozen=True, slots=True)
class SafetyLimits:
    max_voltage_v: float
    max_current_a: float
    max_power_w: float
    max_current_density_a_cm2: float

    def __post_init__(self) -> None:
        for name, value in (
            ("Maximum voltage", self.max_voltage_v),
            ("Maximum current", self.max_current_a),
            ("Maximum power", self.max_power_w),
            ("Maximum current density", self.max_current_density_a_cm2),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise SafetyValidationError(f"{name} must be finite and positive.")


PROJECT_SAFETY_LIMITS = SafetyLimits(10.0, 5.0, 50.0, 5.0)


class SafetyValidator:
    """Thread-safe channel user limits intersected with project/hardware limits."""

    def __init__(
        self,
        channel_limits: dict[int, SafetyLimits] | None = None,
        *,
        project_limits: SafetyLimits = PROJECT_SAFETY_LIMITS,
    ) -> None:
        self._lock = RLock()
        self._project = project_limits
        supplied = channel_limits or {}
        self._user = {channel: supplied.get(channel, project_limits) for channel in (1, 2)}
        for channel, limits in self._user.items():
            QPX600DP_CAPABILITIES.validate_channel(channel)
            if not isinstance(limits, SafetyLimits):
                raise TypeError("channel safety limits must be SafetyLimits")

    def set_user_limits(self, channel: int, limits: SafetyLimits) -> None:
        QPX600DP_CAPABILITIES.validate_channel(channel)
        if not isinstance(limits, SafetyLimits):
            raise TypeError("limits must be SafetyLimits")
        with self._lock:
            self._user[channel] = limits

    def user_limits(self, channel: int) -> SafetyLimits:
        QPX600DP_CAPABILITIES.validate_channel(channel)
        with self._lock:
            return self._user[channel]

    def effective_limits(self, channel: int) -> SafetyLimits:
        user = self.user_limits(channel)
        return SafetyLimits(
            min(user.max_voltage_v, self._project.max_voltage_v, QPX600DP_CAPABILITIES.max_voltage_v),
            min(user.max_current_a, self._project.max_current_a, QPX600DP_CAPABILITIES.max_current_a),
            min(user.max_power_w, self._project.max_power_w, QPX600DP_CAPABILITIES.max_power_w),
            min(user.max_current_density_a_cm2, self._project.max_current_density_a_cm2),
        )

    def validate_operating_point(
        self,
        channel: int,
        voltage_v: float,
        current_a: float,
        voltage_range_name: str,
        *,
        active_area_cm2: float | None = None,
        current_density_a_cm2: float | None = None,
        voltage_label: str = "Voltage",
        current_label: str = "Current",
    ) -> tuple[float, float]:
        """Validate without clamping; capability grids precede policy limits."""
        QPX600DP_CAPABILITIES.validate_channel(channel)
        try:
            voltage = QPX600DP_CAPABILITIES.validate_voltage(voltage_v, voltage_range_name)
            # Zero is the instrument's safe programmed/off baseline.  Protocols
            # independently require a positive current before they acquire a
            # channel; the shared safety layer must still permit validating a
            # freshly connected channel and a harmless 0 A manual setpoint.
            current = (
                0.0
                if float(current_a) == 0.0
                else QPX600DP_CAPABILITIES.validate_current(current_a)
            )
        except InstrumentValueError as error:
            raise SafetyValidationError(str(error)) from error
        limits = self.effective_limits(channel)
        tolerance = 1e-12
        if voltage > limits.max_voltage_v + tolerance:
            raise SafetyValidationError(
                f"{voltage_label} {voltage:g} V exceeds CH{channel} safety limit "
                f"{limits.max_voltage_v:g} V."
            )
        if current > limits.max_current_a + tolerance:
            raise SafetyValidationError(
                f"{current_label} {current:g} A exceeds CH{channel} safety limit "
                f"{limits.max_current_a:g} A."
            )
        power = voltage * current
        if power > limits.max_power_w + tolerance:
            raise SafetyValidationError(
                f"CH{channel} requested power {power:g} W exceeds the effective safety "
                f"limit of {limits.max_power_w:g} W."
            )
        if not QPX600DP_CAPABILITIES.power_envelope.contains(voltage, current):
            raise SafetyValidationError(
                "Requested operating point is outside the verified QPX600DP power envelope."
            )
        density = current_density_a_cm2
        if active_area_cm2 is not None:
            if not math.isfinite(active_area_cm2) or active_area_cm2 <= 0:
                raise SafetyValidationError("Active area must be finite and positive.")
            if density is None:
                density = current / active_area_cm2
            elif not math.isfinite(density) or density < 0:
                raise SafetyValidationError("Current density must be finite and nonnegative.")
        if density is not None and density > limits.max_current_density_a_cm2 + tolerance:
            raise SafetyValidationError(
                f"Commanded current density {density:g} A/cm² exceeds CH{channel} safety "
                f"limit {limits.max_current_density_a_cm2:g} A/cm²."
            )
        return voltage, current

    def summary(self, channel: int) -> str:
        limits = self.effective_limits(channel)
        return (
            f"CH{channel} effective safety limits: {limits.max_voltage_v:g} V, "
            f"{limits.max_current_a:g} A, {limits.max_power_w:g} W, "
            f"{limits.max_current_density_a_cm2:g} A/cm²"
        )
