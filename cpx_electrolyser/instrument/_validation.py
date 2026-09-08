"""Small numeric boundary checks shared by capabilities and simulator models."""

import math
from numbers import Real

from cpx_electrolyser.instrument.exceptions import InstrumentValueError


def finite_number(value: float, name: str) -> float:
    """Accept real numbers, excluding booleans, strings, NaN and infinity."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InstrumentValueError(f"{name} must be a finite real number.")
    try:
        number = float(value)
    except (ValueError, OverflowError) as error:
        raise InstrumentValueError(f"{name} must be a finite real number.") from error
    if not math.isfinite(number):
        raise InstrumentValueError(f"{name} must be a finite real number.")
    return number


def programming_value(value: float, name: str, maximum: float, resolution: float) -> float:
    """Reject unsupported increments; tolerate only floating-point representation error."""
    number = finite_number(value, name)
    if not 0.0 <= number <= maximum:
        raise InstrumentValueError(f"{name} must be between 0 and {maximum:g}.")
    ticks = round(number / resolution)
    canonical = ticks * resolution
    tolerance = 4 * max(math.ulp(number), math.ulp(canonical))
    if not math.isclose(number, canonical, rel_tol=0.0, abs_tol=tolerance):
        raise InstrumentValueError(f"{name} must use increments of {resolution:g}.")
    return canonical
