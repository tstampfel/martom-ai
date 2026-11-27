"""Vector search (M3-3): the products and knowledge chunks whose embedding is closest to a query's.

"Closest" is cosine distance, pgvector's `<=>` operator (0 = same direction, 2 = opposite);
the hits report 1 - distance as `similarity`. Only vectors made by the same model as the
query's are compared (`embedding_model`), because two models' vectors live in different
spaces. Products are filtered exactly as in keyword search (FILTERS_SQL) and read through
`active_olx_listing`, so a sold listing never comes back. Manual products (`manual_product`,
#112) are searched alongside with MANUAL_FILTERS_SQL and merged in by similarity; their hits
have `source="manual"` and no OLX link. Like keyword search, this returns ids and names,
never prices.
"""

from dataclasses import dataclass

import psycopg

from martom.embeddings import Embedder, vector_literal
from martom.search.keyword import (
    FILTERS_SQL,
    MANUAL_FILTERS_SQL,
    HitKey,
    SearchFilters,
    Source,
    filter_params,
    hit_key,
    listing_url,
)

_SEARCH_PRODUCTS = (
    """
SELECT p.listing_id, l.title, l.slug, p.product_type,
       1 - (p.embedding <=> %(vector)s::vector) AS similarity
FROM product AS p
JOIN active_olx_listing AS l ON l.id = p.listing_id
WHERE p.embedding IS NOT NULL
  AND p.embedding_model = %(model)s
  AND
"""
    + FILTERS_SQL
    + """
ORDER BY p.embedding <=> %(vector)s::vector, p.listing_id
LIMIT %(limit)s
"""
)

# A handful of rows: a sequential scan, no index needed.
_SEARCH_MANUAL = (
    """
SELECT p.key, p.name, p.product_type, 1 - (p.embedding <=> %(vector)s::vector) AS similarity
FROM manual_product AS p
WHERE p.embedding IS NOT NULL
  AND p.embedding_model = %(model)s
  AND
"""
    + MANUAL_FILTERS_SQL
    + """
ORDER BY p.embedding <=> %(vector)s::vector, p.key
LIMIT %(limit)s
"""
)

_SEARCH_KNOWLEDGE = """
SELECT id, title, body, status, 1 - (embedding <=> %(vector)s::vector) AS similarity
FROM knowledge_chunk
WHERE embedding IS NOT NULL AND embedding_model = %(model)s
ORDER BY embedding <=> %(vector)s::vector, id
LIMIT %(limit)s
"""


@dataclass(frozen=True)
class VectorHit:
    listing_id: int | None  # None for a manual product (source "manual")
    title: str
    slug: str | None
    product_type: str
    similarity: float  # cosine similarity, 1 = same direction
    source: Source = "olx"
    manual_key: str | None = None  # the manual_products.toml key, for source "manual"

    @property
    def key(self) -> HitKey:
        return hit_key(self.source, self.listing_id, self.manual_key)

    @property
    def url(self) -> str | None:
        return listing_url(self.source, self.slug)


@dataclass(frozen=True)
class KnowledgeHit:
    id: str
    title: str
    body: str
    status: str  # 'draft' or 'approved': the caller decides what a draft may be used for
    similarity: float


async def search_vector(
    conn: psycopg.AsyncConnection,
    vector: list[float],
    model: str,
    filters: SearchFilters | None = None,
    *,
    limit: int = 10,
) -> list[VectorHit]:
    """Active products and manual products closest to `vector` that pass `filters`, closest
    first (at most `limit`)."""
    filters = filters or SearchFilters()
    # With a WHERE clause, an HNSW index scan returns its nearest rows first and the
    # filters run after, which can leave fewer than `limit` rows. pgvector 0.8's iterative
    # scan keeps reading the index until enough rows pass. Set for this query only
    # (is_local=true inside a transaction), like the trigram threshold in keyword search.
    params = {
        "vector": vector_literal(vector),
        "model": model,
        "limit": limit,
        **filter_params(filters),
    }
    async with conn.transaction():
        await conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
        cursor = await conn.execute(_SEARCH_PRODUCTS, params)
        rows = await cursor.fetchall()
        cursor = await conn.execute(_SEARCH_MANUAL, params)
        manual_rows = await cursor.fetchall()
    hits = [
        VectorHit(
            listing_id=row[0],
            title=row[1] or "",
            slug=row[2],
            product_type=row[3],
            similarity=float(row[4]),
        )
        for row in rows
    ] + [
        VectorHit(
            listing_id=None,
            title=row[1] or "",
            slug=None,
            product_type=row[2],
            similarity=float(row[3]),
            source="manual",
            manual_key=row[0],
        )
        for row in manual_rows
    ]
    # Same model, same distance: the two lists merge by similarity. sorted() is stable, so
    # equal similarities keep listings (by id) before manual products.
    hits.sort(key=lambda hit: -hit.similarity)
    return hits[:limit]


async def search_knowledge(
    conn: psycopg.AsyncConnection, query: str, *, embedder: Embedder, limit: int = 5
) -> list[KnowledgeHit]:
    """Toni's knowledge chunks closest to `query`, closest first (drafts included)."""
    vector = await embedder.embed_query(query)
    cursor = await conn.execute(
        _SEARCH_KNOWLEDGE,
        {"vector": vector_literal(vector), "model": embedder.model, "limit": limit},
    )
    return [
        KnowledgeHit(id=row[0], title=row[1], body=row[2], status=row[3], similarity=float(row[4]))
        for row in await cursor.fetchall()
    ]
