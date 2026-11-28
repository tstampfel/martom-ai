"""The quantity and price of a cart (korpa) line (#147; Tomislav's rule, 28 Nov 2026).

A korpa quantity or price never comes from a number in the model's or the customer's
text, only from tool output:

- A product on the current materials list (the BOM calculator's latest result) goes in
  with that line's quantity and unit price.
- Any other product goes in as 1 piece, priced from the catalogue (get_product's
  database row, or manual_products.toml); the customer changes the quantity in the korpa.

The model never sets a quantity: add_to_cart has no quantity argument. The tool builds
the line with `bom_quantity`, and api/chat_turn.py checks every line again with
`matches_bom` before a `cart` event goes out.

A BOM line is linked to a product through its price source (`price_sources`, which
calculate_fence_bom adds next to the lines: the line's `price_from`, or its own item and
size), then price_map.toml (BOM item + size -> listing id, or a manual product's key).
So a roll fence's support T-posts (`brace_t_post`, priced as the `post_t` of the same
size) link to the T-post too, and one cart line gets the sum of every line that shares
the product (#147, review R3-1). The link doesn't depend on a price: on a draft map the
quantity still comes from the list and the price stays null ("na upit"). One listing
holds panels, posts, clips and anchors as its variants, so for an OLX product the
variant being added must also fit the map entry's variant table (`_label_fits`).
"""

# The size sign "×" is real data here (listing labels write it).
# ruff: noqa: RUF002, RUF003

import functools
import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from martom.agent.grounding import numbers_in
from martom.bom.common import BomLine, Item, PriceRef
from martom.bom.pricing import CM_FIELDS, Match, PriceMap, load_price_map, normalise

logger = logging.getLogger(__name__)

DEFAULT_QUANTITY = 1
_ITEMS = frozenset(item.value for item in Item)


def price_sources(lines: Sequence[BomLine]) -> list[dict[str, str | None]]:
    """Each line's price source (item + size), for calculate_fence_bom's output.

    It sits next to the bom event's lines (the event itself doesn't carry it), so the cart
    can link a line priced through `price_from` to that product.
    """
    sources = []
    for line in lines:
        ref = line.price_from or PriceRef(line.item, line.size)
        sources.append({"item": ref.item.value, "size": ref.size})
    return sources


def bom_quantity(
    product: Mapping[str, Any],
    bom: Mapping[str, Any] | None,
    price_map: PriceMap | None = None,
) -> tuple[int, str | None] | None:
    """The quantity and unit price a cart line takes from the materials list, or None
    when the product isn't on it.

    Several lines priced from one product (T-posts and support T-posts) add up; they
    share one unit price by construction. Raises CartQuantityError when they don't.
    """
    lines = bom_lines_for(product, bom, price_map)
    if not lines:
        return None
    unit_prices = {line.get("unit_price") for line in lines}
    if len(unit_prices) > 1:
        raise CartQuantityError("the materials list prices this product differently")
    return sum(int(line["quantity"]) for line in lines), unit_prices.pop()


class CartQuantityError(ValueError):
    """The materials list gives no single quantity and price for this product."""


def bom_lines_for(
    product: Mapping[str, Any],
    bom: Mapping[str, Any] | None,
    price_map: PriceMap | None = None,
) -> list[dict[str, Any]]:
    """The lines of the materials list priced from this product (variant).

    `product` has a cart line's keys: source, listing_id, manual_key, variant_label.
    """
    links = price_map if price_map is not None else shipped_price_map()
    lines = list((bom or {}).get("lines", []))
    sources = list((bom or {}).get("price_sources") or [])
    if len(sources) != len(lines):  # an older list without them: each line's own key
        sources = [{"item": line.get("item"), "size": line.get("size")} for line in lines]
    return [
        line
        for line, source in zip(lines, sources, strict=True)
        if _is_this_product(product, source, links)
    ]


def matches_bom(cart_line: Mapping[str, Any], bom: Mapping[str, Any] | None) -> bool:
    """Whether a cart line follows the rule: the list's (summed) quantity and price, or 1."""
    try:
        from_list = bom_quantity(cart_line, bom)
    except CartQuantityError:
        return False
    if from_list is None:
        return cart_line.get("quantity") == DEFAULT_QUANTITY
    return (cart_line.get("quantity"), cart_line.get("unit_price")) == from_list


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
    product: Mapping[str, Any], source: Mapping[str, Any], price_map: PriceMap | None
) -> bool:
    """Whether a BOM line whose price source is `source` (item + size) is this product."""
    item, size = source.get("item"), source.get("size")
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
