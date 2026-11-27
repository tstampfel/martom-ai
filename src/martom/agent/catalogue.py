"""Product lookup for the advisor's tools: one active listing with its variants and prices.

Reads `active_olx_listing` (never `olx_listing` directly, CLAUDE.md), joined to the
extracted `product` and `product_variant` rows. Prices here are database values, so the
grounding check accepts them when the model repeats them.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import psycopg

from martom.search.keyword import LISTING_URL
from martom.search.query_parser import parse_height_cm


@dataclass(frozen=True)
class VariantInfo:
    label: str
    price: Decimal | None  # None: no parsed price ("na upit")
    unit: str | None
    height: str | None  # as written


@dataclass(frozen=True)
class ProductInfo:
    listing_id: int
    title: str
    slug: str | None
    product_type: str | None
    colours: list[str]
    listing_price: Decimal | None  # None for 0 KM or "po dogovoru"
    variants: list[VariantInfo]

    @property
    def url(self) -> str | None:
        return LISTING_URL.format(slug=self.slug) if self.slug else None


class Catalogue(Protocol):
    """What the tools need from the catalogue; tests pass an in-memory fake."""

    async def get_product(self, listing_id: int) -> ProductInfo | None: ...

    async def variant_heights(self, listing_id: int) -> list[int]: ...


_PRODUCT_SQL = """
SELECT l.id, l.title, l.slug, p.product_type, coalesce(p.colours, '{}'),
       CASE WHEN l.price > 0 AND NOT coalesce(l.price_by_agreement, false)
            THEN l.price END
FROM active_olx_listing l
LEFT JOIN product p ON p.listing_id = l.id
WHERE l.id = %s
"""

_VARIANTS_SQL = """
SELECT label, CASE WHEN price > 0 THEN price END, unit, height
FROM product_variant
WHERE listing_id = %s
ORDER BY position
"""


class PostgresCatalogue:
    def __init__(self, conn: psycopg.AsyncConnection) -> None:
        self.conn = conn

    async def get_product(self, listing_id: int) -> ProductInfo | None:
        async with self.conn.cursor() as cur:
            await cur.execute(_PRODUCT_SQL, (listing_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            await cur.execute(_VARIANTS_SQL, (listing_id,))
            variants = [VariantInfo(*variant) for variant in await cur.fetchall()]
        listing, title, slug, product_type, colours, price = row
        return ProductInfo(listing, title or "", slug, product_type, list(colours), price, variants)

    async def variant_heights(self, listing_id: int) -> list[int]:
        """The heights (cm) the listing's variants write, parsed in Python, sorted."""
        product = await self.get_product(listing_id)
        if product is None:
            return []
        heights = {parse_height_cm(variant.height) for variant in product.variants}
        return sorted(round(height) for height in heights if height is not None)
