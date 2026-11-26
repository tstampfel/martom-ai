"""manual_products: products Toni sells without an OLX listing (fence-rules.md)."""

# ruff: noqa: RUF001

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from martom.bom.common import Item
from martom.bom.manual_products import (
    MAX_AGE_DAYS,
    ManualProduct,
    load_manual_products,
    stale_manual_products,
)


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "manual_products.toml"
    path.write_text(text, encoding="utf-8")
    return path


def product(last_confirmed: date) -> ManualProduct:
    return ManualProduct(
        key="anchor_m8x70",
        name="Anker M8×70",
        price_km=Decimal("0.80"),
        source="Toni 26.11.2026",
        last_confirmed=last_confirmed,
    )


def test_committed_config_has_the_m8x70_anchor_at_0_80_km() -> None:
    products = load_manual_products()

    anchor = products["anchor_m8x70"]
    assert anchor.name == "Anker M8×70"
    assert anchor.price_km == Decimal("0.80")
    assert anchor.source == "Toni 26.11.2026"
    assert anchor.last_confirmed == date(2026, 11, 26)


def test_loads_a_config_file_given_as_a_path(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
[anchor_m8x70]
name = "Vijak"
price_km = "0.15"
source = "Toni 1.12.2026"
last_confirmed = 2026-12-01
""",
    )

    assert load_manual_products(path) == {
        "anchor_m8x70": ManualProduct(
            key="anchor_m8x70",
            name="Vijak",
            price_km=Decimal("0.15"),
            source="Toni 1.12.2026",
            last_confirmed=date(2026, 12, 1),
        )
    }


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('price_km = "0.15"\nsource = "Toni"\nlast_confirmed = 2026-12-01', "name"),
        ('name = "Vijak"\nsource = "Toni"\nlast_confirmed = 2026-12-01', "price_km"),
        ('name = "Vijak"\nprice_km = "0.15"\nlast_confirmed = 2026-12-01', "source"),
        ('name = "Vijak"\nprice_km = "0.15"\nsource = "Toni"', "last_confirmed"),
        # A float price would carry binary rounding error: prices are strings.
        ('name = "Vijak"\nprice_km = 0.15\nsource = "Toni"\nlast_confirmed = 2026-12-01', "price"),
        ('name = "Vijak"\nprice_km = "abc"\nsource = "Toni"\nlast_confirmed = 2026-12-01', "price"),
        ('name = "Vijak"\nprice_km = "-1"\nsource = "Toni"\nlast_confirmed = 2026-12-01', "price"),
        ('name = "Vijak"\nprice_km = "0.15"\nsource = "Toni"\nlast_confirmed = "2026"', "date"),
        ('name = ""\nprice_km = "0.15"\nsource = "Toni"\nlast_confirmed = 2026-12-01', "name"),
    ],
)
def test_rejects_an_incomplete_or_malformed_entry(tmp_path: Path, entry: str, message: str) -> None:
    path = write_config(tmp_path, f"[anchor_m8x70]\n{entry}\n")

    with pytest.raises(ValueError, match=message):
        load_manual_products(path)


def test_entry_confirmed_exactly_90_days_ago_is_not_stale() -> None:
    today = date(2027, 2, 24)
    entry = product(last_confirmed=date(2026, 11, 26))  # 90 days before today

    assert MAX_AGE_DAYS == 90
    assert stale_manual_products({"anchor_m8x70": entry}, today=today) == []


def test_entry_older_than_90_days_is_stale() -> None:
    today = date(2027, 2, 25)
    entry = product(last_confirmed=date(2026, 11, 26))  # 91 days before today

    assert stale_manual_products({"anchor_m8x70": entry}, today=today) == [entry]


def test_committed_config_is_not_stale_on_the_day_toni_confirmed_it() -> None:
    assert stale_manual_products(load_manual_products(), today=date(2026, 11, 26)) == []


def test_rejects_a_table_name_that_is_not_a_bom_item(tmp_path: Path) -> None:
    # A typo would load fine and leave the BOM line without a price at display time.
    path = write_config(
        tmp_path,
        """
[anchor_m8_x70]
name = "Anker M8×70"
price_km = "0.80"
source = "Toni"
last_confirmed = 2026-11-26
""",
    )

    with pytest.raises(ValueError, match="anchor_m8_x70"):
        load_manual_products(path)


def test_every_item_without_an_olx_listing_has_an_entry() -> None:
    assert Item.ANCHOR_M8X70.value in load_manual_products()


def test_stale_check_accepts_a_datetime_for_today() -> None:
    entry = product(last_confirmed=date(2026, 11, 26))
    today = datetime(2027, 2, 25, 10, 0, tzinfo=UTC)  # 91 days later

    assert stale_manual_products({"anchor_m8x70": entry}, today=today) == [entry]
