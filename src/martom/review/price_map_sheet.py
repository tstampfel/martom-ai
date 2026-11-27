"""Toni's review sheet for the BOM price map (#125): one Markdown row per materials item.

`scripts/price_map_review_sheet.py` reads `price_map.toml` and the database (read-only)
and writes the sheet to `data/` (git-ignored: it holds catalogue data), so Tomislav can
turn it into a PDF and Toni can tick "OK" or write what is wrong on each row. Plain Python,
no model. The text is in Croatian; prices are database (or manual TOML) values, and links
come only from `listing_url(slug)`.

The welded roll has no mapping yet. Tomislav's working assumption until Toni confirms:
rolls are sold whole (25 m) at 25 × the per-metre price. The sheet shows each per-metre
candidate variant within 5 cm of the roll height with that 25 m price marked
"za potvrdu"; the materials list itself keeps the roll "cijena na upit".
"""

# Croatian labels use the real "×" and "–" signs.
# ruff: noqa: RUF001, RUF002, RUF003

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import psycopg

from martom.bom.common import Item, PriceRef
from martom.bom.manual_products import ManualProduct
from martom.bom.pricing import (
    PriceMap,
    PriceMapEntry,
    matches_variant,
    normalise,
    written_cm,
)
from martom.extract.store import VariantRow
from martom.review.checks import format_km
from martom.review.report import listing_url

HEADER = (
    "Stavka",
    "Veličina / boja",
    "OLX oglas",
    "Varijanta (kako piše u oglasu)",
    "Cijena",
    "Link",
    "OK",
    "Pogrešno / napomena",
)

ITEM_NAMES: dict[Item, str] = {
    Item.PANEL: "Panel",
    Item.POST_BASE_PLATE: "Stup s pločicom",
    Item.PVC_CLIP: "PVC kopča",
    Item.ANCHOR_M8X80: "Anker M8×80",
    Item.ROLL_WELDED: "Varena ograda u roli",
    Item.POST_T: "T-stup",
    Item.POST_FOOT_PLATE: "Stup sa stopom",
    Item.BRACE_T_POST: "Potporni T-stup",
    Item.TENSIONER: "Zatezač (španer)",
    Item.TENSION_WIRE: "Nosiva žica (kolut 100 m)",
    Item.WIRE_HOLDER: "Nosač nosive žice",
    Item.ANCHOR_M8X70: "Anker M8×70",
}

ROLL_LENGTH_M = 25  # fence-rules.md: a roll is 25 m
ROLL_HEIGHT_TOLERANCE_CM = 5  # the same ±5 cm as the search height filter

NOT_ACTIVE = "oglas nije aktivan ili nije obrađen"
NOT_MAPPED = "nije povezano"


@dataclass(frozen=True)
class SheetListing:
    id: int
    title: str
    slug: str | None
    variants: list[VariantRow]


@dataclass(frozen=True)
class SheetRow:
    item: str
    key: str
    listing: str
    variant: str
    price: str
    link: str
    note: str


def build_rows(
    price_map: PriceMap,
    listings: Mapping[int, SheetListing],
    manual_products: Mapping[str, ManualProduct],
    *,
    roll_candidates: Sequence[SheetListing],
) -> list[SheetRow]:
    """One row per variant table of each map entry, then one per unmapped line.

    `listings` are the active, extracted listings the map points at; `roll_candidates`
    the active listings that might be the welded roll (per-metre variants are offered).
    """
    rows: list[SheetRow] = []
    for entry in price_map.entries.values():
        rows += _entry_rows(entry, listings, manual_products)
    for ref, reason in price_map.unmapped.items():
        candidate_rows = (
            _roll_rows(ref, reason, roll_candidates) if ref.item == Item.ROLL_WELDED else []
        )
        rows += candidate_rows or [
            SheetRow(ITEM_NAMES[ref.item], ref.size or "–", NOT_MAPPED, "", "", "", reason)
        ]
    return rows


def _entry_rows(
    entry: PriceMapEntry,
    listings: Mapping[int, SheetListing],
    manual_products: Mapping[str, ManualProduct],
) -> list[SheetRow]:
    name, size = ITEM_NAMES[entry.item], entry.size or "–"
    if entry.manual_key is not None:
        product = manual_products.get(entry.manual_key)
        if product is None:
            return [SheetRow(name, size, "bez OLX oglasa", "", "", "", "nema u manual_products")]
        return [
            SheetRow(name, size, "bez OLX oglasa (manual_products.toml)", product.name,
                     format_km(product.price_km), "",
                     f"izvor: {product.source}, potvrđeno {product.last_confirmed:%d.%m.%Y}.")
        ]  # fmt: skip
    listing = listings.get(entry.listing_id) if entry.listing_id is not None else None
    rows = []
    for match in entry.matches:
        colour = dict(match).get("colour")
        key = f"boja: {colour}" if len(entry.matches) > 1 and colour else size
        if listing is None:
            rows.append(SheetRow(name, key, f"oglas {entry.listing_id}", "", "", "", NOT_ACTIVE))
            continue
        found = [v for v in listing.variants if matches_variant(v, match)]
        variant, price, note = "", "", ""
        if len(found) == 1:
            variant = found[0].label
            if found[0].price is not None and found[0].price > 0:
                price = format_km(found[0].price)
            else:
                note = "varijanta nema cijenu"
        else:
            note = "varijanta nije pronađena" if not found else "više varijanti odgovara"
        rows.append(
            SheetRow(name, key, listing.title, variant, price, listing_url(listing.slug) or "",
                     note)
        )  # fmt: skip
    return rows


def _roll_rows(ref: PriceRef, reason: str, candidates: Sequence[SheetListing]) -> list[SheetRow]:
    """Per-metre candidate variants within 5 cm of the roll height, priced × 25 m."""
    height = written_cm(ref.size)
    if height is None:
        return []
    rows = []
    for listing in candidates:
        for variant in listing.variants:
            variant_height = written_cm(variant.height)
            if (
                variant_height is None
                or abs(variant_height - height) > ROLL_HEIGHT_TOLERANCE_CM
                or not _per_metre(variant)
                or variant.price is None
                or variant.price <= 0
            ):
                continue
            roll_price = variant.price * ROLL_LENGTH_M
            price = (
                f"{format_km(roll_price)} za {ROLL_LENGTH_M} m "
                f"({ROLL_LENGTH_M} × {format_km(variant.price)}/m) – za potvrdu"
            )
            rows.append(
                SheetRow(ITEM_NAMES[ref.item], ref.size or "–", listing.title, variant.label,
                         price, listing_url(listing.slug) or "", f"{NOT_MAPPED}: {reason}")
            )  # fmt: skip
    return rows


def _per_metre(variant: VariantRow) -> bool:
    """The variant's unit says "per metre" ("m", "/M"); only without a unit, its price
    text ("6,00KM/m")."""
    if variant.unit:
        return normalise(variant.unit).strip("/") == "m"
    return normalise(variant.price_text or "").endswith("/m")


def render_markdown(rows: Sequence[SheetRow], *, today: date, status: str) -> str:
    status_text = "pregledana" if status == "reviewed" else "nacrt (još se ne koristi za cijene)"
    lines = [
        "# Cijene za popis materijala ograde – provjera za Tonija",
        "",
        f"Stanje kataloga: {today:%d.%m.%Y}. Mapa cijena: {status_text}.",
        "",
        "Za svaki red stavite ✓ u stupac „OK” ako je oglas, varijanta i cijena ispravna, "
        "ili upišite što je pogrešno. Redovi „nije povezano” kupcu prikazuju „cijena na "
        "upit”. Cijene označene „za potvrdu” su pretpostavka (rola od 25 m = 25 × cijena "
        "po metru) i kupcu se još ne prikazuju.",
        "",
        "| " + " | ".join(HEADER) + " |",
        "|" + "---|" * len(HEADER),
    ]
    for row in rows:
        cells = (row.item, row.key, row.listing, row.variant, row.price, row.link, "", row.note)
        lines.append("| " + " | ".join(_cell(cell) for cell in cells[:6]) + " |  | "
                     + _cell(cells[7]) + " |")  # fmt: skip
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    """One table cell: no line breaks, a "|" escaped."""
    return " ".join(text.split()).replace("|", "\\|")


# --- database (read-only) ----------------------------------------------------------------

_LISTINGS_SQL = """
SELECT l.id, l.title, l.slug, v.label, v.height, v.length, v.colour, v.size, v.price_text,
       v.price, v.unit
FROM active_olx_listing AS l
JOIN product AS p ON p.listing_id = l.id
LEFT JOIN product_variant AS v ON v.listing_id = p.listing_id
WHERE {where}
ORDER BY l.id, v.position
"""

# Roll listings that might be the welded roll: "rol" in the title or product type, but not
# chain link (pletivo, baklava), which has other rules (fence-rules.md).
_ROLL_WHERE = """
(l.title ILIKE '%%rol%%' OR p.product_type ILIKE '%%rol%%')
AND l.title NOT ILIKE '%%baklav%%' AND l.title NOT ILIKE '%%pletiv%%'
AND p.product_type NOT ILIKE '%%baklav%%' AND p.product_type NOT ILIKE '%%pletiv%%'
"""


def load_listings(conn: psycopg.Connection, listing_ids: Sequence[int]) -> dict[int, SheetListing]:
    return _load(conn, "l.id = ANY(%s)", (list(listing_ids),))


def load_roll_candidates(conn: psycopg.Connection) -> list[SheetListing]:
    return list(_load(conn, _ROLL_WHERE, ()).values())


def _load(conn: psycopg.Connection, where: str, params: tuple) -> dict[int, SheetListing]:
    rows = conn.execute(_LISTINGS_SQL.format(where=where), params).fetchall()  # type: ignore[arg-type]
    listings: dict[int, SheetListing] = {}
    for listing_id, title, slug, *variant in rows:
        listing = listings.setdefault(listing_id, SheetListing(listing_id, title or "", slug, []))
        if variant[0] is not None:
            listing.variants.append(VariantRow(*variant))
    return listings


def entry_listing_ids(price_map: PriceMap) -> list[int]:
    return sorted({e.listing_id for e in price_map.entries.values() if e.listing_id})
