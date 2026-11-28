"""The quantity and price of a cart (korpa) line (#147; Tomislav's rule, 28 Nov 2026).

A korpa quantity or price never comes from a number in the model's or the customer's
text, only from tool output:

- A product on the current materials list (the BOM calculator's latest result) goes in
  with that line's quantity and unit price.
- Any other product goes in as 1 piece, priced from the catalogue (get_product's
  database row, or manual_products.toml); the customer changes the quantity in the korpa.

The model never sets a quantity: add_to_cart has no quantity argument. The tool builds
the line with `bom_lines_for`, and api/chat_turn.py checks every line again with
`matches_bom` before a `cart` event goes out.

A BOM line is linked to a product through price_map.toml (BOM item + size -> listing id,
or a manual product's key). One listing holds panels, posts, clips and anchors as its
variants, so for an OLX product the variant being added must also fit the map entry's
variant table (`_label_fits`).
"""

# The size sign "×" is real data here (listing labels write it).
# ruff: noqa: RUF002, RUF003

import functools
import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from martom.agent.grounding import numbers_in
from martom.bom.common import Item, PriceRef
from martom.bom.pricing import CM_FIELDS, Match, PriceMap, load_price_map, normalise

logger = logging.getLogger(__name__)

DEFAULT_QUANTITY = 1
_ITEMS = frozenset(item.value for item in Item)


def bom_lines_for(
    product: Mapping[str, Any],
    bom: Mapping[str, Any] | None,
    price_map: PriceMap | None = None,
) -> list[dict[str, Any]]:
    """The lines of the materials list priced from this product (variant).

    `product` has a cart line's keys: source, listing_id, manual_key, variant_label.
    """
    links = price_map if price_map is not None else shipped_price_map()
    return [line for line in (bom or {}).get("lines", []) if _is_this_product(product, line, links)]


def matches_bom(cart_line: Mapping[str, Any], bom: Mapping[str, Any] | None) -> bool:
    """Whether a cart line follows the rule: the BOM line's quantity and price, or 1."""
    lines = bom_lines_for(cart_line, bom)
    if not lines:
        return cart_line.get("quantity") == DEFAULT_QUANTITY
    if len(lines) > 1:
        return False
    line = lines[0]
    return cart_line.get("quantity") == line.get("quantity") and cart_line.get(
        "unit_price"
    ) == line.get("unit_price")


@functools.cache
def shipped_price_map() -> PriceMap | None:
    """price_map.toml, read once. A broken map links only manual products (their key is
    the BOM item); the review run reports the broken map itself."""
    try:
        return load_price_map()
    except (OSError, ValueError) as exc:
        logger.warning("price map unreadable for the cart", extra={"error": str(exc)})
        return None


def _is_this_product(
    product: Mapping[str, Any], bom_line: Mapping[str, Any], price_map: PriceMap | None
) -> bool:
    item, size = bom_line.get("item"), bom_line.get("size")
    if product.get("source") == "manual" and item == product.get("manual_key"):
        return True
    if price_map is None or item not in _ITEMS:
        return False
    entry = price_map.entries.get(PriceRef(Item(item), size))
    if entry is None:
        return False
    if product.get("source") == "manual":
        return entry.manual_key is not None and entry.manual_key == product.get("manual_key")
    if entry.listing_id is None or entry.listing_id != product.get("listing_id"):
        return False
    return any(_label_fits(product.get("variant_label"), match) for match in entry.matches)


def _label_fits(label: str | None, match: Match) -> bool:
    """Whether the variant label fits one variant table of the map entry.

    Only the fields a label shows are checked: a height or length in cm ("153 × 250" has
    153 and 250), `label_contains` and `size`. A table with none of them (the tensioner's
    colour and unit) fits any variant of its listing.
    """
    numbers = set(numbers_in(label or ""))
    for field, wanted in match:
        if field in CM_FIELDS and Decimal(wanted) not in numbers:
            return False
        if field in {"label_contains", "size"} and (
            label is None or normalise(str(wanted)) not in normalise(label)
        ):
            return False
    return True
