"""`martom embed` (M3-3): embed products and knowledge chunks whose text changed.

What is embedded:
- a product: its `search_text` (the same compact text keyword search indexes; the
  marketing description is left out for the same reason), only for listings in
  `active_olx_listing`;
- a knowledge chunk: its title and body;
- a manual product (`manual_product`, #112): its `search_text`, built from the
  manual_products.toml name (search/manual.py).

A row is embedded again only when `text_hash(model, text)` differs from its stored
`embedding_hash`: its text changed (a re-extraction, Toni edited a chunk) or the model did.
Run it after `martom extract` and `martom load-knowledge`. Rows are saved one batch at a
time, so a run that fails halfway keeps what it already embedded.
"""

import logging
from dataclasses import dataclass

import psycopg

from martom.embeddings import Embedder, text_hash, vector_literal

logger = logging.getLogger(__name__)

_PRODUCTS = """
SELECT p.listing_id, p.search_text, p.embedding_hash
FROM product AS p
JOIN active_olx_listing AS l ON l.id = p.listing_id
ORDER BY p.listing_id
"""
_SAVE_PRODUCT = """
UPDATE product
SET embedding = %s::vector, embedding_model = %s, embedding_hash = %s
WHERE listing_id = %s
"""
_MANUAL = "SELECT key, search_text, embedding_hash FROM manual_product ORDER BY key"
_SAVE_MANUAL = """
UPDATE manual_product
SET embedding = %s::vector, embedding_model = %s, embedding_hash = %s
WHERE key = %s
"""
_CHUNKS = "SELECT id, title, body, embedding_hash FROM knowledge_chunk ORDER BY id"
_SAVE_CHUNK = """
UPDATE knowledge_chunk
SET embedding = %s::vector, embedding_model = %s, embedding_hash = %s
WHERE id = %s
"""


@dataclass(frozen=True)
class EmbedStats:
    products_embedded: int
    products_unchanged: int
    chunks_embedded: int
    chunks_unchanged: int
    manual_embedded: int = 0
    manual_unchanged: int = 0


def knowledge_text(title: str, body: str) -> str:
    """The text embedded for a knowledge chunk."""
    return f"{title}\n\n{body}"


async def _embed_rows(
    conn: psycopg.AsyncConnection,
    embedder: Embedder,
    rows: list[tuple[object, str, str | None]],
    save_sql: str,
) -> tuple[int, int]:
    """Embed the (key, text, stored hash) rows whose hash changed; (embedded, unchanged)."""
    todo = [
        (key, text, text_hash(embedder.model, text))
        for key, text, stored in rows
        if stored != text_hash(embedder.model, text)
    ]
    for start in range(0, len(todo), embedder.batch_size):
        batch = todo[start : start + embedder.batch_size]
        vectors = await embedder.embed_documents([text for _, text, _ in batch])
        async with conn.cursor() as cursor:
            await cursor.executemany(
                save_sql,
                [
                    (vector_literal(vector), embedder.model, new_hash, key)
                    for (key, _, new_hash), vector in zip(batch, vectors, strict=True)
                ],
            )
        logger.info(
            "embedded batch",
            extra={"model": embedder.model, "done": start + len(batch), "total": len(todo)},
        )
    return len(todo), len(rows) - len(todo)


async def run_embedding(conn: psycopg.AsyncConnection, embedder: Embedder) -> EmbedStats:
    """Embed every changed active product, knowledge chunk and manual product (see the
    module docstring)."""
    cursor = await conn.execute(_PRODUCTS)
    products = [(row[0], row[1], row[2]) for row in await cursor.fetchall()]
    products_embedded, products_unchanged = await _embed_rows(
        conn, embedder, products, _SAVE_PRODUCT
    )

    cursor = await conn.execute(_CHUNKS)
    chunks = [(row[0], knowledge_text(row[1], row[2]), row[3]) for row in await cursor.fetchall()]
    chunks_embedded, chunks_unchanged = await _embed_rows(conn, embedder, chunks, _SAVE_CHUNK)

    cursor = await conn.execute(_MANUAL)
    manual = [(row[0], row[1], row[2]) for row in await cursor.fetchall()]
    manual_embedded, manual_unchanged = await _embed_rows(conn, embedder, manual, _SAVE_MANUAL)

    stats = EmbedStats(
        products_embedded,
        products_unchanged,
        chunks_embedded,
        chunks_unchanged,
        manual_embedded,
        manual_unchanged,
    )
    logger.info("embedding run finished", extra={"model": embedder.model, **stats.__dict__})
    return stats
