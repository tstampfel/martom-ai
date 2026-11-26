"""Products Toni sells that have no OLX listing, from the reviewed manual_products.toml.

fence-rules.md, "Products without an OLX listing (`manual_products`)": each entry has a
name, a price, a source and a `last_confirmed` date, and an entry more than 90 days old
goes to Toni's review queue. Raising that review_issue is #10's job; this module only
loads the file and lists the stale entries.
"""

import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from martom.bom.common import Item

DEFAULT_PATH = Path(__file__).with_name("manual_products.toml")

# fence-rules.md: "more than 90 days old" raises a review item.
MAX_AGE_DAYS = 90


@dataclass(frozen=True)
class ManualProduct:
    key: str  # the table name in the TOML file, the same as the BOM item value
    name: str
    price_km: Decimal
    source: str
    last_confirmed: date


def load_manual_products(path: Path = DEFAULT_PATH) -> dict[str, ManualProduct]:
    """Read the config file; raise ValueError naming the first bad entry and field."""
    with path.open("rb") as file:  # tomllib wants bytes
        data = tomllib.load(file)
    item_keys = {item.value for item in Item}
    for key in data:
        # The table name is the BOM item key; a typo would leave the line without a price.
        if key not in item_keys:
            raise ValueError(f"manual product {key!r} is not a BOM item ({sorted(item_keys)})")
    return {key: _product(key, entry) for key, entry in data.items()}


def stale_manual_products(
    products: dict[str, ManualProduct], *, today: date
) -> list[ManualProduct]:
    """The entries whose last confirmation is more than MAX_AGE_DAYS before `today`."""
    # A datetime is a date too, but datetime - date raises TypeError: keep the day only.
    if isinstance(today, datetime):
        today = today.date()
    return [p for p in products.values() if (today - p.last_confirmed).days > MAX_AGE_DAYS]


def _product(key: str, entry: Any) -> ManualProduct:
    if not isinstance(entry, dict):
        raise ValueError(f"manual product {key!r} must be a table")
    return ManualProduct(
        key=key,
        name=_text(key, entry, "name"),
        price_km=_price(key, entry),
        source=_text(key, entry, "source"),
        last_confirmed=_date(key, entry),
    )


def _text(key: str, entry: dict[str, Any], field: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manual product {key!r}: {field} must be a non-empty string")
    return value


def _price(key: str, entry: dict[str, Any]) -> Decimal:
    value = entry.get("price_km")
    # A TOML float (0.80) would carry binary rounding error, so only a string is allowed.
    if not isinstance(value, str):
        raise ValueError(f'manual product {key!r}: price_km must be a string such as "0.80"')
    try:
        price = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"manual product {key!r}: price_km {value!r} is not a number") from None
    if not price.is_finite() or price < 0:
        raise ValueError(f"manual product {key!r}: price_km {value!r} is not a valid price")
    return price


def _date(key: str, entry: dict[str, Any]) -> date:
    value = entry.get("last_confirmed")
    if value is None:
        raise ValueError(f"manual product {key!r}: last_confirmed is missing")
    # TOML has a native date type (last_confirmed = 2026-11-26); a datetime is a date
    # subclass in Python, so rule it out to keep the field a plain calendar date.
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"manual product {key!r}: last_confirmed must be a date (YYYY-MM-DD)")
    return value
