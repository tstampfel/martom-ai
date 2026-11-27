"""Hybrid search (M3-3): keyword search and vector search, merged with reciprocal rank fusion.

Reciprocal rank fusion (RRF) merges ranked lists without comparing their scores (a
full-text rank and a cosine similarity are on different scales): each list gives an id
1 / (k + rank), and the sums decide the order. An id near the top of both lists wins.

How `search_hybrid` runs:

1. Keyword search (search/keyword.py) and vector search (search/vector.py) each return
   their best `candidates` listings, with the same filters.
2. A keyword hit found by trigram similarity alone (a typo match, `text_match` False) is
   dropped when the listing has a vector for this model and vector search did not return
   it. Typo matching is the weakest evidence: "kolica" is 0.57 word-similar to the
   grinder's "brusilica kutna" (#102), while a real typo such as "panelan" is also close in
   meaning to the panels it should find. A listing vector search can't judge (no vector
   yet for this model: new since the last `martom embed`, or the model just changed) keeps
   its typo match.
3. RRF merges the two lists; the first `limit` are returned.

Both searches include manual products (#112). Hits are matched across the lists by
`HitKey` (("olx", listing id) or ("manual", TOML key)), so a listing and a manual product
never share an id, and a manual hit keeps `source="manual"` and no link.

If the query can't be embedded (provider down, rate limit, timeout), the search answers
with keyword results alone and logs a warning: a slower or worse search beats no search.
Evals pass `fallback=False` instead: no timeout, and an embedding error is raised, so a
keyword-only answer is never scored as hybrid.

Out of scope here (M3-4): rerank, and parsing filters out of the question.
"""

import asyncio
import logging
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

import psycopg

from martom.embeddings import Embedder
from martom.search.keyword import (
    HitKey,
    KeywordHit,
    SearchFilters,
    Source,
    hit_key,
    key_from_row,
    listing_url,
    search_keyword,
    strip_filler_words,
)
from martom.search.vector import VectorHit, search_vector

logger = logging.getLogger(__name__)

# The usual RRF constant (Cormack et al., 2009). A bigger k flattens the difference
# between rank 1 and rank 10; 60 is the value most systems use unchanged.
RRF_K = 60
# How many results each search contributes before fusion. 30 is three times the 10 that
# recall@10 reads, so a listing ranked low by one search can still be lifted by the other.
DEFAULT_CANDIDATES = 30
# The most a search waits for its query's embedding before answering with keyword results
# alone. Covers every provider, including a local model still loading.
QUERY_EMBED_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class HybridHit:
    listing_id: int | None  # None for a manual product (source "manual")
    title: str
    slug: str | None
    product_type: str
    score: float  # the RRF score
    keyword_rank: int | None  # 1-based rank in each list, None when that search missed it
    vector_rank: int | None
    source: Source = "olx"
    manual_key: str | None = None  # the manual_products.toml key, for source "manual"

    @property
    def key(self) -> HitKey:
        return hit_key(self.source, self.listing_id, self.manual_key)

    @property
    def url(self) -> str | None:
        return listing_url(self.source, self.slug)


# `[Id: Hashable]` makes this generic, like a TS `<Id>`: any id that can be a dict key and
# be sorted (an int listing id in the tests, a HitKey in search_hybrid).
def reciprocal_rank_fusion[Id: Hashable](
    rankings: Sequence[Sequence[Id]], *, k: int = RRF_K
) -> list[tuple[Id, float]]:
    """(id, score) pairs, best first. Ranks are 1-based; an id counts once per list.

    Equal scores are ordered by the id's best single rank, then by the smaller id, so the
    result never depends on dict order.
    """
    scores: dict[Id, float] = {}
    best_rank: dict[Id, int] = {}
    for ranking in rankings:
        seen: set[Id] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank)
            best_rank[item] = min(best_rank.get(item, rank), rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], best_rank[pair[0]], pair[0]))


async def search_hybrid(
    conn: psycopg.AsyncConnection,
    query: str,
    filters: SearchFilters | None = None,
    *,
    embedder: Embedder,
    limit: int = 10,
    candidates: int = DEFAULT_CANDIDATES,
    fallback: bool = True,
) -> list[HybridHit]:
    """Active products for `query` and `filters`, best first (see the module docstring)."""
    if not strip_filler_words(query):
        return []  # nothing but filler words: nothing to search for, nothing to embed
    keyword = await search_keyword(conn, query, filters, limit=candidates)
    vector = await _vector_hits(conn, query, filters, embedder, candidates, fallback=fallback)
    if vector is None:
        vector = []  # keyword results only; every typo match stays
    else:
        if not vector:
            logger.warning(
                "vector search returned nothing; has `martom embed` run for this model?",
                extra={"embedding_model": embedder.model},
            )
        keyword = await _drop_rejected_typo_matches(conn, keyword, vector, embedder.model)

    keyword_ranks = {hit.key: rank for rank, hit in enumerate(keyword, start=1)}
    vector_ranks = {hit.key: rank for rank, hit in enumerate(vector, start=1)}
    # Title, slug and type are the same in both lists; take them from whichever has the id.
    details: dict[HitKey, KeywordHit | VectorHit] = {hit.key: hit for hit in vector} | {
        hit.key: hit for hit in keyword
    }
    fused = reciprocal_rank_fusion([[hit.key for hit in keyword], [hit.key for hit in vector]])
    return [
        HybridHit(
            listing_id=details[key].listing_id,
            title=details[key].title,
            slug=details[key].slug,
            product_type=details[key].product_type,
            score=score,
            keyword_rank=keyword_ranks.get(key),
            vector_rank=vector_ranks.get(key),
            source=details[key].source,
            manual_key=details[key].manual_key,
        )
        for key, score in fused[:limit]
    ]


async def _vector_hits(
    conn: psycopg.AsyncConnection,
    query: str,
    filters: SearchFilters | None,
    embedder: Embedder,
    candidates: int,
    *,
    fallback: bool,
) -> list[VectorHit] | None:
    """Vector search's hits, or None when the query couldn't be embedded in time.

    With fallback=False there is no time limit and an embedding error is raised.
    """
    if not fallback:
        vector = await embedder.embed_query(query)
        return await search_vector(conn, vector, embedder.model, filters, limit=candidates)
    try:
        vector = await asyncio.wait_for(
            embedder.embed_query(query), timeout=QUERY_EMBED_TIMEOUT_SECONDS
        )
    except Exception as exc:
        # Any provider error (HTTP, auth, rate limit), EmbeddingError or the timeout: the
        # customer still gets keyword results. Logged with its type, never the query text
        # in the message (it is customer text).
        logger.warning(
            "query embedding failed; keyword results only",
            extra={"embedding_model": embedder.model, "error": type(exc).__name__},
        )
        return None
    return await search_vector(conn, vector, embedder.model, filters, limit=candidates)


async def _drop_rejected_typo_matches(
    conn: psycopg.AsyncConnection,
    keyword: list[KeywordHit],
    vector: list[VectorHit],
    model: str,
) -> list[KeywordHit]:
    """Keyword hits without the typo matches vector search had a chance to find and didn't."""
    vector_keys = {hit.key for hit in vector}
    unconfirmed = [hit for hit in keyword if not hit.text_match and hit.key not in vector_keys]
    if not unconfirmed:
        return keyword
    listing_ids = [hit.listing_id for hit in unconfirmed if hit.source == "olx"]
    manual_keys = [hit.manual_key for hit in unconfirmed if hit.source == "manual"]
    cursor = await conn.execute(
        "SELECT 'olx', listing_id::text FROM product"
        " WHERE listing_id = ANY(%(ids)s) AND embedding IS NOT NULL"
        " AND embedding_model = %(model)s"
        " UNION ALL"
        " SELECT 'manual', key FROM manual_product"
        " WHERE key = ANY(%(keys)s) AND embedding IS NOT NULL AND embedding_model = %(model)s",
        {"ids": listing_ids, "keys": manual_keys, "model": model},
    )
    rejected: set[HitKey] = {
        key_from_row(source, value) for source, value in await cursor.fetchall()
    }
    return [hit for hit in keyword if hit.key not in rejected]
