"""Database side of extraction: read listings to extract, write `product` rows.

Reads only the `active_olx_listing` view, never `olx_listing` (CLAUDE.md): a sold listing
is never extracted. Plain SQL through psycopg 3's async API, like `martom.olx.store`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from martom.extract.prompt import ListingInput

# A listing needs extraction when its detail was fetched (detail_hash set) and it has no
# product row yet, or the row was made from another detail hash. IS DISTINCT FROM is "!="
# that also treats NULL as a value.
_PENDING = """
SELECT l.id, l.title, l.description_text, l.detail_hash, l.display_price, l.price,
       l.price_by_agreement, l.state, l.brand, l.attributes
FROM active_olx_listing AS l
LEFT JOIN product AS p ON p.listing_id = l.id
WHERE l.detail_hash IS NOT NULL
  AND (%(force_all)s OR p.detail_hash IS DISTINCT FROM l.detail_hash)
ORDER BY l.id
LIMIT %(limit)s
"""


def _attributes(value: Any) -> tuple[tuple[str, str], ...]:
    """olx_listing.attributes is jsonb: [{"name": ..., "value": ...}, ...]."""
    return tuple(
        (str(a["name"]), str(a["value"])) for a in value or () if a.get("value") is not None
    )


async def load_pending(
    conn: psycopg.AsyncConnection, *, force_all: bool = False, limit: int | None = None
) -> list[ListingInput]:
    """Active listings to extract, by id. `force_all` ignores the hashes (`--all`)."""
    cursor = await conn.execute(_PENDING, {"force_all": force_all, "limit": limit})
    return [
        ListingInput(
            id=row[0],
            title=row[1] or "",
            description_text=row[2] or "",
            detail_hash=row[3],
            display_price=row[4],
            price=row[5],
            price_by_agreement=bool(row[6]),
            state=row[7],
            brand=row[8],
            attributes=_attributes(row[9]),
        )
        for row in await cursor.fetchall()
    ]


@dataclass(frozen=True)
class VariantRow:
    label: str
    height: str | None
    length: str | None
    colour: str | None
    size: str | None
    price_text: str | None
    price: Decimal | None
    unit: str | None


@dataclass(frozen=True)
class ProductRecord:
    """One `product` row and its `product_variant` rows, ready to save."""

    listing_id: int
    detail_hash: str
    product_type: str
    brand: str | None
    material: str | None
    wire_thickness: str | None
    mesh: str | None
    colours: list[str]
    uses: list[str]
    compatible_with: list[str]
    synonyms: list[str]
    extraction: dict[str, Any]
    model: str
    prompt_version: str
    search_text: str
    variants: Sequence[VariantRow]


_UPSERT_PRODUCT = """
INSERT INTO product (listing_id, detail_hash, product_type, brand, material, wire_thickness,
                     mesh, colours, uses, compatible_with, synonyms, extraction, model,
                     prompt_version, search_text)
VALUES (%(listing_id)s, %(detail_hash)s, %(product_type)s, %(brand)s, %(material)s,
        %(wire_thickness)s, %(mesh)s, %(colours)s, %(uses)s, %(compatible_with)s,
        %(synonyms)s, %(extraction)s, %(model)s, %(prompt_version)s, %(search_text)s)
ON CONFLICT (listing_id) DO UPDATE SET
    detail_hash = EXCLUDED.detail_hash,
    product_type = EXCLUDED.product_type,
    brand = EXCLUDED.brand,
    material = EXCLUDED.material,
    wire_thickness = EXCLUDED.wire_thickness,
    mesh = EXCLUDED.mesh,
    colours = EXCLUDED.colours,
    uses = EXCLUDED.uses,
    compatible_with = EXCLUDED.compatible_with,
    synonyms = EXCLUDED.synonyms,
    extraction = EXCLUDED.extraction,
    model = EXCLUDED.model,
    prompt_version = EXCLUDED.prompt_version,
    search_text = EXCLUDED.search_text,
    extracted_at = now()
"""

_INSERT_VARIANT = """
INSERT INTO product_variant (listing_id, position, label, height, length, colour, size,
                             price_text, price, unit)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


async def save_products(conn: psycopg.AsyncConnection, records: Sequence[ProductRecord]) -> None:
    """Upsert products and replace their variants, all in one transaction."""
    if not records:
        return
    async with conn.transaction(), conn.cursor() as cursor:
        for record in records:
            await cursor.execute(
                _UPSERT_PRODUCT,
                {
                    "listing_id": record.listing_id,
                    "detail_hash": record.detail_hash,
                    "product_type": record.product_type,
                    "brand": record.brand,
                    "material": record.material,
                    "wire_thickness": record.wire_thickness,
                    "mesh": record.mesh,
                    "colours": record.colours,
                    "uses": record.uses,
                    "compatible_with": record.compatible_with,
                    "synonyms": record.synonyms,
                    "extraction": Jsonb(record.extraction),
                    "model": record.model,
                    "prompt_version": record.prompt_version,
                    "search_text": record.search_text,
                },
            )
            # Replace, don't merge: the new extraction is the whole truth for the listing.
            await cursor.execute(
                "DELETE FROM product_variant WHERE listing_id = %s", (record.listing_id,)
            )
            await cursor.executemany(
                _INSERT_VARIANT,
                [
                    (
                        record.listing_id,
                        position,
                        v.label,
                        v.height,
                        v.length,
                        v.colour,
                        v.size,
                        v.price_text,
                        v.price,
                        v.unit,
                    )
                    for position, v in enumerate(record.variants)
                ],
            )
