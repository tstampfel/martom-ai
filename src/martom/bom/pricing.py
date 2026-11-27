"""Which synced price a BOM line uses: the reviewed price map and the exact-match rules (#125).

The BOM calculators return quantities only. `price_map.toml` (data, reviewed in a PR)
maps each BOM item + size to an OLX listing and the variant(s) that price it (heights and
lengths in centimetres, colour / size / unit and a piece of the label as extraction wrote
them), or to a `manual_products.toml` entry. This module
loads that file and resolves one entry against the variants the database returned.

Plain Python, no database: the DB pricer (`martom.agent.pricer`) and Toni's review check
(`martom.review.checks.check_bom_price_map`) both call `resolve_price`, so the customer
and Toni see the same verdict. A price comes only from a variant row or the manual TOML;
anything unmapped, inactive, missing, ambiguous, unpriced or with colours that disagree is
None, which the BOM card shows as "cijena na upit". Never a guess.
"""

import re
import tomllib
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from martom.bom.common import BomLine, Item, PriceRef, is_int
from martom.bom.manual_products import ManualProduct
from martom.extract.store import VariantRow

DEFAULT_PATH = Path(__file__).with_name("price_map.toml")

# What a variant table in the map may name, and the product_variant column it reads.
# Never `price` / `price_text`, and never the whole label (extraction sometimes writes the
# price into it): a match must not depend on the price.
#   height_cm, length_cm : a whole number of centimetres; the column must be one number
#                          with an optional "H-" and mm / cm / m ("H-125", "H-125 cm",
#                          "125 cm" and "1250 mm" are all 125)
#   colour, size, unit   : the column as written, compared normalised (case, accents and
#                          spaces ignored: "2,70MM" == "2,70 mm")
#   label_contains       : normalised text the label must contain ("PVC kopča")
# `unit` is required, so each entry states what one BOM quantity is priced per (a piece, a
# panel, a 100 m coil), and a per-metre price can never price a per-piece line.
CM_FIELDS = {"height_cm": "height", "length_cm": "length"}
TEXT_FIELDS = {"colour": "colour", "size": "size", "unit": "unit", "label_contains": "label"}
MATCH_FIELDS = frozenset(CM_FIELDS) | frozenset(TEXT_FIELDS)

type Status = Literal["draft", "reviewed"]
STATUSES: tuple[Status, ...] = ("draft", "reviewed")

# One variant match: (field, value) pairs, e.g. (("height_cm", 153), ("unit", "panel")).
type Match = tuple[tuple[str, str | int], ...]

# One number and an optional unit, nothing else: "H-125", "h - 125CM", "250 cm", "1250 mm".
_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")
_ONE_SIZE = re.compile(
    r"(?:h\s*[-.]?\s*)?(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(mm|cm|m)?",
    re.IGNORECASE,
)
_CM_PER = {"mm": Decimal("0.1"), "cm": Decimal(1), "m": Decimal(100), None: Decimal(1)}


@dataclass(frozen=True)
class PriceMapEntry:
    item: Item
    size: str | None
    listing_id: int | None  # set for an OLX entry
    matches: tuple[Match, ...]  # OLX entry: one match per variant, all must agree on price
    manual_key: str | None = None  # set for a manual_products.toml entry

    @property
    def ref(self) -> PriceRef:
        return PriceRef(self.item, self.size)


@dataclass(frozen=True)
class PriceMap:
    status: Status
    entries: dict[PriceRef, PriceMapEntry]
    unmapped: dict[PriceRef, str]  # BOM lines deliberately left "cijena na upit", with why


class PriceProblem(StrEnum):
    """Why a mapped line has no price. The review check reports each one to Toni."""

    LISTING_INACTIVE = "listing_inactive"  # not in active_olx_listing, or not extracted
    VARIANT_MISSING = "variant_missing"  # no variant has the mapped fields
    VARIANT_AMBIGUOUS = "variant_ambiguous"  # more than one variant has them
    UNPRICED = "unpriced"  # the variant has no price above 0 KM ("na upit")
    PRICES_DIFFER = "prices_differ"  # the mapped colours no longer cost the same
    MANUAL_MISSING = "manual_missing"  # no such table in manual_products.toml


@dataclass(frozen=True)
class PriceLookup:
    price: Decimal | None
    problem: PriceProblem | None = None
    # The variant tables behind the problem: the one that is missing, ambiguous or
    # unpriced, or all of them when their prices differ (Toni's message quotes these).
    failed_matches: tuple[Match, ...] = ()


def normalise(text: str) -> str:
    """Casefold, drop accents and every space: "Cinčani 2,70 MM" -> "cincani2,70mm"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c) and not c.isspace())


def written_cm(text: str | None) -> Decimal | None:
    """The centimetres of a field that holds one size ("H-125 cm" -> 125), else None.

    "125 x 250 cm" or "153 maslinasto" hold more than one number or a word: None, so such
    a field never matches a height (no guess).
    """
    found = _ONE_SIZE.fullmatch(text.strip()) if text else None
    if found is None:
        return None
    unit = found.group(2).lower() if found.group(2) else None
    number = written_number(found.group(1), in_metres=unit == "m")
    return number * _CM_PER[unit]


def written_number(digits: str, *, in_metres: bool) -> Decimal:
    """A number as listings write it: "153", "4,20", "4.20"; a dot before exactly three
    digits is a thousands separator ("1.550" = 1550), except in metres without a comma
    ("2.500 m" = 2,5 m: nobody writes thousands of metres). The one rule, also used by
    the review checks' size parsing (`martom.review.checks`)."""
    if _THOUSANDS.fullmatch(digits) and not (in_metres and "," not in digits):
        digits = digits.replace(".", "")
    return Decimal(digits.replace(",", "."))


def matches_variant(variant: VariantRow, match: Match) -> bool:
    """Whether the variant has every field of one variant table of the map."""
    for field, wanted in match:
        if field in CM_FIELDS:
            if written_cm(getattr(variant, CM_FIELDS[field])) != Decimal(wanted):
                return False
            continue
        written = getattr(variant, TEXT_FIELDS[field])
        if written is None:
            return False
        if field == "label_contains":
            if normalise(str(wanted)) not in normalise(written):
                return False
        elif normalise(written) != normalise(str(wanted)):
            return False
    return True


def lookup_ref(line: BomLine) -> PriceRef | None:
    """The price-map key of a line, or None when the line must never show a price.

    A line marked `unit_price_on_request` (e.g. the 100 cm brace before the sync has it)
    or `made_to_order` is "cijena na upit" whatever the map says. Otherwise the line's
    `price_from` (a support T-post is priced as the T-post) or its own item and size.
    """
    if line.unit_price_on_request or line.made_to_order:
        return None
    return line.price_from or PriceRef(line.item, line.size)


def resolve_price(
    entry: PriceMapEntry,
    variants: Sequence[VariantRow] | None,
    manual_products: Mapping[str, ManualProduct],
) -> PriceLookup:
    """The entry's unit price, or None with the reason.

    `variants` are the extracted variants of `entry.listing_id`, or None when that listing
    is not active (or has no product row yet). Every match must fit exactly one variant
    (`matches_variant`); each of those variants needs a price above 0; and when there are
    several matches (colours) their prices must be equal. A manual product needs a price
    above 0 too: 0 KM is never a price.
    """
    if entry.manual_key is not None:
        product = manual_products.get(entry.manual_key)
        if product is None:
            return PriceLookup(None, PriceProblem.MANUAL_MISSING)
        if not product.price_km.is_finite() or product.price_km <= 0:
            return PriceLookup(None, PriceProblem.UNPRICED)
        return PriceLookup(product.price_km)
    if variants is None:
        return PriceLookup(None, PriceProblem.LISTING_INACTIVE)

    prices: set[Decimal] = set()
    for match in entry.matches:
        found = [v for v in variants if matches_variant(v, match)]
        if not found:
            return PriceLookup(None, PriceProblem.VARIANT_MISSING, (match,))
        if len(found) > 1:
            return PriceLookup(None, PriceProblem.VARIANT_AMBIGUOUS, (match,))
        price = found[0].price
        if price is None or not price.is_finite() or price <= 0:
            return PriceLookup(None, PriceProblem.UNPRICED, (match,))
        prices.add(price)
    if len(prices) != 1:
        return PriceLookup(None, PriceProblem.PRICES_DIFFER, entry.matches)
    return PriceLookup(prices.pop())


# --- loading price_map.toml -------------------------------------------------------------


def load_price_map(path: Path = DEFAULT_PATH) -> PriceMap:
    """Read and validate the map; raise ValueError naming the first bad entry."""
    with path.open("rb") as file:  # tomllib wants bytes
        data = tomllib.load(file)
    unknown = set(data) - {"status", "entry", "unmapped"}
    if unknown:
        raise ValueError(f"price map: unknown key(s) {sorted(unknown)}")
    status = data.get("status")
    if status not in STATUSES:
        raise ValueError(f"price map: status must be one of {STATUSES}, got {status!r}")

    entries: dict[PriceRef, PriceMapEntry] = {}
    unmapped: dict[PriceRef, str] = {}
    seen: set[PriceRef] = set()
    for raw in _tables(data, "entry"):
        entry = _entry(raw)
        _add_once(seen, entry.ref)
        entries[entry.ref] = entry
    for raw in _tables(data, "unmapped"):
        _check_keys(raw, {"item", "size", "reason"}, "unmapped")
        ref = PriceRef(_item(raw), _size(raw))
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"price map: unmapped {_name(ref)} needs a reason")
        _add_once(seen, ref)
        unmapped[ref] = reason
    return PriceMap(status=status, entries=entries, unmapped=unmapped)


def _tables(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    tables = data.get(key, [])
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        raise ValueError(f"price map: {key} must be an array of tables ([[{key}]])")
    return tables


def _entry(raw: dict[str, Any]) -> PriceMapEntry:
    _check_keys(raw, {"item", "size", "listing_id", "variants", "manual"}, "entry")
    ref = PriceRef(_item(raw), _size(raw))
    name = _name(ref)
    has_listing, has_manual = "listing_id" in raw, "manual" in raw
    if has_listing == has_manual:
        raise ValueError(f"price map: {name} needs exactly one of listing_id or manual")
    if has_manual:
        manual = raw["manual"]
        if not isinstance(manual, str) or not manual.strip():
            raise ValueError(f"price map: {name}: manual must be a manual_products.toml key")
        if "variants" in raw:
            raise ValueError(f"price map: {name}: a manual entry takes no variants")
        return PriceMapEntry(ref.item, ref.size, listing_id=None, matches=(), manual_key=manual)
    listing_id = raw["listing_id"]
    # is_int rules out a bool (true == 1 in Python) and a quoted "63996799".
    if not is_int(listing_id) or listing_id <= 0:
        raise ValueError(f"price map: {name}: listing_id must be a positive integer")
    variants = raw.get("variants")
    if not isinstance(variants, list) or not variants:
        raise ValueError(f"price map: {name}: variants must list at least one variant")
    matches = tuple(_match(name, match) for match in variants)
    return PriceMapEntry(ref.item, ref.size, listing_id=listing_id, matches=matches)


def _match(name: str, raw: object) -> Match:
    if not isinstance(raw, dict):
        raise ValueError(f"price map: {name}: each variant must be a table, e.g. {{ unit = ... }}")
    if "unit" not in raw:
        raise ValueError(f"price map: {name}: each variant needs a unit (what it is priced per)")
    for field, value in raw.items():
        if field not in MATCH_FIELDS:
            raise ValueError(
                f"price map: {name}: field {field!r} is not one of {sorted(MATCH_FIELDS)}"
            )
        if field in CM_FIELDS:
            if not is_int(value) or value <= 0:
                raise ValueError(f"price map: {name}: {field} must be a positive whole number")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"price map: {name}: {field} must be a non-empty string")
    return tuple(raw.items())


def _check_keys(raw: dict[str, Any], allowed: set[str], kind: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"price map: {kind} has unknown key(s) {sorted(unknown)}")


def _item(raw: dict[str, Any]) -> Item:
    try:
        return Item(raw.get("item"))
    except ValueError:
        choices = sorted(item.value for item in Item)
        raise ValueError(
            f"price map: item {raw.get('item')!r} is not a BOM item ({choices})"
        ) from None


def _size(raw: dict[str, Any]) -> str | None:
    size = raw.get("size")
    if size is not None and (not isinstance(size, str) or not size.strip()):
        raise ValueError(f"price map: size {size!r} must be a non-empty string")
    return size


def _add_once(seen: set[PriceRef], ref: PriceRef) -> None:
    if ref in seen:
        raise ValueError(f"price map: {_name(ref)} is listed twice")
    seen.add(ref)


def _name(ref: PriceRef) -> str:
    return f"{ref.item.value} {ref.size}" if ref.size else ref.item.value
