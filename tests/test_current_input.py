"""Shared current/current-density conversion and validation tests."""

from decimal import Decimal

import pytest

from qpx_electrolyser.engine.current_input import (
    CurrentInputMode,
    CurrentInputSelection,
)


def test_direct_current_and_density_units_convert_exactly() -> None:
    direct = CurrentInputSelection.parse(CurrentInputMode.CURRENT).resolve(
        "0.08", "Current"
    )
    assert direct.current_a == Decimal("0.08")
    assert direct.current_density_a_cm2 is None
    assert direct.active_area_cm2 is None

    density = CurrentInputSelection.parse("current_density", "2.5").resolve(
        "0.04", "Current density"
    )
    assert density.current_a == Decimal("0.100")
    assert density.current_density_a_cm2 == Decimal("0.04")
    assert density.active_area_cm2 == Decimal("2.5")


@pytest.mark.parametrize("area", ["", "0", "-1", "nan", "inf", "-inf", "text"])
def test_density_requires_finite_positive_active_area(area: str) -> None:
    with pytest.raises(ValueError, match="Active area"):
        CurrentInputSelection.parse("current_density", area)


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="mode"):
        CurrentInputSelection.parse("milliamps", "1")
