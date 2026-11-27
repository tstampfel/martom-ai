"""The real `BomPricer` (#125): BOM line prices from the synced catalogue, through the map.

Reads the price map (`martom.bom.pricing`, `price_map.toml`) and, for a mapped OLX entry,
the extracted variants of that listing from `product_variant` joined to
`active_olx_listing` (never `olx_listing` directly, CLAUDE.md), so a sold listing has no
variants and its lines are "cijena na upit". Manual entries are priced from
`manual_products.toml` (never from the `manual_product` search table). The exact-match
rules live in `resolve_price`; this class only fetches the rows.

A draft map (`status` other than "reviewed") prices nothing: every line is "cijena na
upit" until Tomislav and Toni review it (Tomislav, #125 R1-7); building the pricer from a
draft logs one warning.

A database error while pricing one line makes that line "cijena na upit" and logs a
warning; it never loses the materials list. Each line queries on its own (the
`BomPricer` protocol prices one line at a time), so in the rare case that extraction
rewrites a listing between two lines of one BOM, each line still shows a real database
price. Prices follow the latest extraction: a listing changed on OLX keeps its extracted
price until `martom extract` (run right after each sync) re-extracts it, the same window
the review check and `get_product` have.
"""

import logging
from collections.abc import Mapping
from decimal import Decimal

import psycopg

from martom.bom.common import BomLine
from martom.bom.manual_products import ManualProduct, load_manual_products
from martom.bom.pricing import PriceMap, load_price_map, lookup_ref, resolve_price
from martom.extract.store import VariantRow

logger = logging.getLogger(__name__)

# LEFT JOIN: an active listing with a product row but no variants gives one row of NULLs
# (an empty list), an inactive or unextracted listing gives no row (None).
_VARIANTS_SQL = """
SELECT v.label, v.height, v.length, v.colour, v.size, v.price_text, v.price, v.unit
FROM active_olx_listing AS l
JOIN product AS p ON p.listing_id = l.id
LEFT JOIN product_variant AS v ON v.listing_id = p.listing_id
WHERE l.id = %s
ORDER BY v.position
"""


class PostgresBomPricer:
    """Implements `martom.agent.tools.BomPricer`.

    The map and the manual products are read once, when the pricer is built (a broken
    file raises ValueError then, never during a chat). Pass them in tests. `conn` must be
    in autocommit mode: a connection idle in a transaction would hold locks between
    chats, and after one error every later query would fail.
    """

    def __init__(
        self,
        conn: psycopg.AsyncConnection,
        *,
        price_map: PriceMap | None = None,
        manual_products: Mapping[str, ManualProduct] | None = None,
    ) -> None:
        if not conn.autocommit:
            raise ValueError("PostgresBomPricer needs an autocommit connection")
        self.conn = conn
        self.price_map = price_map if price_map is not None else load_price_map()
        self.manual_products = (
            manual_products if manual_products is not None else load_manual_products()
        )
        if self.price_map.status != "reviewed":
            logger.warning("price map is a draft: every BOM line is on request")

    async def unit_price(self, line: BomLine) -> Decimal | None:
        if self.price_map.status != "reviewed":
            return None  # a draft map prices nothing
        ref = lookup_ref(line)
        entry = self.price_map.entries.get(ref) if ref is not None else None
        if entry is None:
            return None  # on request, made to order or not mapped: "cijena na upit"
        variants = None
        if entry.listing_id is not None:
            try:
                variants = await self._variants(entry.listing_id)
            except psycopg.Error as exc:
                logger.warning(
                    "bom price lookup failed",
                    extra={
                        "listing_id": entry.listing_id,
                        "item": entry.item.value,
                        "size": entry.size,
                        "error": type(exc).__name__,
                    },
                )
                return None
        return resolve_price(entry, variants, self.manual_products).price

    async def _variants(self, listing_id: int) -> list[VariantRow] | None:
        async with self.conn.cursor() as cur:
            await cur.execute(_VARIANTS_SQL, (listing_id,))
            rows = await cur.fetchall()
        if not rows:
            return None
        return [VariantRow(*row) for row in rows if row[0] is not None]
