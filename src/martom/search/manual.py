"""Manual products in search (#112): keep `manual_product` in step with manual_products.toml.

Products Toni sells without an OLX listing (today only "Anker M8×70") live in the reviewed
`src/martom/bom/manual_products.toml`. Search reads `manual_product`, an index of that file:
keyword search over its normalised text, vector search over its embedding (`martom embed`),
the same as `product` for OLX listings. Hits from it say `source="manual"`, carry the TOML
key instead of a listing id, and never have an OLX link.

`sync_manual_products` upserts one row per entry, skips entries whose content hash is
unchanged and deletes rows whose entry is gone, all in one transaction, so running it twice
changes nothing. `martom load-manual-products` runs it alone, `martom migrate` runs it after
the migrations (so a deploy that runs migrate updates search at once), and `martom embed`
runs it before embedding, so the nightly chain keeps search in step with the TOML too.

The price is copied only for the search price filter. A price shown to a customer comes
from the TOML (grounding), never from this table.
"""

# Comments quote the name with the real "×" sign, as in the TOML.
# ruff: noqa: RUF001, RUF002, RUF003

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

import psycopg

from martom.bom.manual_products import ManualProduct

# "×" (the multiplication sign Toni writes in sizes) becomes "*" after unaccent, so a
# customer's "M8x70" would never match "M8×70". The search text holds both spellings.
_TIMES = "×"


@dataclass(frozen=True)
class ManualSyncStats:
    inserted: int
    updated: int
    unchanged: int
    deleted: int


def manual_search_text(product: ManualProduct) -> str:
    """The text keyword search indexes, embedding and the reranker read for a manual product.

    The name as written, then the plain-"x" spelling of each word that has a "×"
    ("Anker M8×70" -> "Anker M8×70 M8x70"). Every other word appears once: ts_rank_cd counts
    each occurrence, and keyword search merges manual and listing scores directly, so a
    repeated name would weigh twice as much as the same words in a listing (R1-0).
    """
    plain = [word.replace(_TIMES, "x") for word in product.name.split() if _TIMES in word]
    return " ".join([product.name, *plain])


def _content_hash(product: ManualProduct, search_text: str) -> str:
    fields = {"name": product.name, "price_km": str(product.price_km), "search_text": search_text}
    encoded = json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ON CONFLICT ... WHERE: an unchanged row isn't rewritten, and RETURNING then gives no row
# for it. `xmax = 0` is true for a freshly inserted row (Postgres's way to tell an insert
# from an update in one upsert).
_UPSERT = """
INSERT INTO manual_product (key, name, product_type, price_km, search_text, content_hash)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (key) DO UPDATE SET
    name = EXCLUDED.name,
    product_type = EXCLUDED.product_type,
    price_km = EXCLUDED.price_km,
    search_text = EXCLUDED.search_text,
    content_hash = EXCLUDED.content_hash,
    loaded_at = now()
WHERE manual_product.content_hash IS DISTINCT FROM EXCLUDED.content_hash
RETURNING (xmax = 0) AS inserted
"""


async def sync_manual_products(
    conn: psycopg.AsyncConnection, products: Mapping[str, ManualProduct]
) -> ManualSyncStats:
    """Make `manual_product` mirror `products` (see the module docstring)."""
    inserted = updated = 0
    async with conn.transaction():
        for product in products.values():
            search_text = manual_search_text(product)
            cursor = await conn.execute(
                _UPSERT,
                (
                    product.key,
                    product.name,
                    # No product type is written in the TOML; the name stands in for it, so
                    # a type filter such as "anker" matches "Anker M8×70".
                    product.name,
                    product.price_km,
                    search_text,
                    _content_hash(product, search_text),
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                continue  # unchanged
            if row[0]:
                inserted += 1
            else:
                updated += 1
        cursor = await conn.execute(
            "DELETE FROM manual_product WHERE key <> ALL(%s)", (list(products),)
        )
        deleted = cursor.rowcount
    return ManualSyncStats(
        inserted=inserted,
        updated=updated,
        unchanged=len(products) - inserted - updated,
        deleted=deleted,
    )
