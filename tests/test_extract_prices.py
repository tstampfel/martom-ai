"""`parse_price_km`: the price text a listing writes -> a number, in plain Python."""

from decimal import Decimal

import pytest

from martom.extract.prices import parse_price_km


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("27,00 KM", Decimal("27.00")),
        ("88,00KM", Decimal("88.00")),
        ("-0,15KM", Decimal("0.15")),  # "SX6, -0,15KM": the dash is a separator
        ("0,75KM", Decimal("0.75")),
        ("44 KM", Decimal("44")),
        ("1.250,00 KM", Decimal("1250.00")),  # dot = thousands, comma = decimals
        ("12,25", Decimal("12.25")),
        ("➡️ 89,00 KM", Decimal("89.00")),
        ("13.50 KM", Decimal("13.50")),  # a dot with two decimals is a decimal point
    ],
)
def test_parses_prices_as_written(text: str, expected: Decimal) -> None:
    assert parse_price_km(text) == expected


@pytest.mark.parametrize("text", [None, "", "Na upit", "po dogovoru", "27,00 / 31,00 KM"])
def test_unreadable_or_ambiguous_price_is_none(text: str | None) -> None:
    # None means "no number"; the deterministic checks (M2-2) flag it for Toni.
    assert parse_price_km(text) is None
