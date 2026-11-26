"""Shared BOM input checks (martom.bom.common), used by the panel and roll calculators."""

import pytest

from martom.bom.common import FenceLimit, FenceTooLargeError, sides_in_cm
from martom.bom.panel import panel_fence_bom
from martom.bom.roll import roll_fence_bom

# Exponents near or past Decimal's Emax: scaling them to centimetres overflows.
HUGE_SIDES = ["9e999999", "1e9999999999", 9e307]


@pytest.mark.parametrize("side", HUGE_SIDES)
def test_huge_side_is_too_large_not_a_decimal_overflow(side: str | float) -> None:
    with pytest.raises(FenceTooLargeError) as error:
        sides_in_cm([side])
    assert error.value.limit == FenceLimit.SIDE


@pytest.mark.parametrize("side", HUGE_SIDES)
def test_panel_fence_with_a_huge_side_is_too_large(side: str | float) -> None:
    with pytest.raises(FenceTooLargeError):
        panel_fence_bom(sides_m=[side], panel_height_cm=153)


@pytest.mark.parametrize("side", HUGE_SIDES)
def test_roll_fence_with_a_huge_side_is_too_large(side: str | float) -> None:
    with pytest.raises(FenceTooLargeError):
        roll_fence_bom(sides_m=[side], roll_height_cm=80, post_model="t_post")
