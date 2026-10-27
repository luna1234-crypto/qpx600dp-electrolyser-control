"""Shared typed current/current-density conversion for electrochemical protocols."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class CurrentInputMode(str, Enum):
    """Operator-facing ways to specify a commanded current."""

    CURRENT = "current"
    CURRENT_DENSITY = "current_density"


@dataclass(frozen=True, slots=True)
class ResolvedCurrent:
    """One exact operator value normalized to instrument amperes."""

    current_a: Decimal
    input_mode: CurrentInputMode
    active_area_cm2: Decimal | None = None
    current_density_a_cm2: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CurrentInputSelection:
    """Validated mode/area context shared by M9, M13 and future M14."""

    mode: CurrentInputMode
    active_area_cm2: Decimal | None = None

    @classmethod
    def parse(
        cls,
        mode: str | CurrentInputMode,
        active_area_cm2: str = "",
    ) -> CurrentInputSelection:
        try:
            selected = mode if isinstance(mode, CurrentInputMode) else CurrentInputMode(mode)
        except (TypeError, ValueError) as error:
            raise ValueError("Current input mode must be current or current density.") from error
        if selected is CurrentInputMode.CURRENT:
            return cls(selected)
        area = _decimal(active_area_cm2, "Active area")
        if area <= 0:
            raise ValueError("Active area must be positive.")
        return cls(selected, area)

    def resolve(
        self,
        value: str | Decimal,
        label: str,
    ) -> ResolvedCurrent:
        exact = value if isinstance(value, Decimal) else _decimal(value, label)
        if self.mode is CurrentInputMode.CURRENT:
            return ResolvedCurrent(exact, self.mode)
        assert self.active_area_cm2 is not None
        return ResolvedCurrent(
            current_a=exact * self.active_area_cm2,
            input_mode=self.mode,
            active_area_cm2=self.active_area_cm2,
            current_density_a_cm2=exact,
        )


def _decimal(value: str, label: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required.")
    try:
        exact = Decimal(value.strip())
    except InvalidOperation as error:
        raise ValueError(f"{label} must be numeric.") from error
    if not exact.is_finite():
        raise ValueError(f"{label} must be finite.")
    return exact
