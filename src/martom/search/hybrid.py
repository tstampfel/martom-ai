"""Hybrid search (M3-3): keyword search and vector search, merged with reciprocal rank fusion.

Reciprocal rank fusion (RRF) merges ranked lists without comparing their scores (a
full-text rank and a cosine similarity are on different scales): each list gives an id
1 / (k + rank), and the sums decide the order. An id near the top of both lists wins.

How `search_hybrid` runs:

1. Keyword search (search/keyword.py) and vector search (search/vector.py) each return
   their best `candidates` listings, with the same filters.
2. A keyword hit found by trigram similarity alone (a typo match, `text_match` False) is
   kept only when vector search also returned that listing. Typo matching is the weakest
   evidence: "kolica" is 0.57 word-similar to the grinder's "brusilica kutna" (#102), while
   a real typo such as "panelan" is also close in meaning to the panels it should find.
3. RRF merges the two lists; the first `limit` are returned.

Out of scope here (M3-4): rerank, and parsing filters out of the question.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import psycopg

from martom.embeddings import Embedder
from martom.search.keyword import LISTING_URL, SearchFilters, search_keyword, strip_filler_words
from martom.search.vector import search_vector

# The usual RRF constant (Cormack et al., 2009). A bigger k flattens the difference
# between rank 1 and rank 10; 60 is the value most systems use unchanged.
RRF_K = 60
# How many results each search contributes before fusion. 30 is three times the 10 that
# recall@10 reads, so a listing ranked low by one search can still be lifted by the other.
DEFAULT_CANDIDATES = 30


@dataclass(frozen=True)
class HybridHit:
    listing_id: int
    title: str
    slug: str | None
    product_type: str
    score: float  # the RRF score
    keyword_rank: int | None  # 1-based rank in each list, None when that search missed it
    vector_rank: int | None

    @property
    def url(self) -> str | None:
        return LISTING_URL.format(slug=self.slug) if self.slug else None


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]], *, k: int = RRF_K
) -> list[tuple[int, float]]:
    """(id, score) pairs, best first. Ranks are 1-based; an id counts once per list.

    Equal scores are ordered by the id's best single rank, then by the smaller id, so the
    result never depends on dict order.
    """
    scores: dict[int, float] = {}
    best_rank: dict[int, int] = {}
    for ranking in rankings:
        seen: set[int] = set()
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
) -> list[HybridHit]:
    """Active products for `query` and `filters`, best first (see the module docstring)."""
    if not strip_filler_words(query):
        return []  # nothing but filler words: nothing to search for, nothing to embed
    keyword = await search_keyword(conn, query, filters, limit=candidates)
    vector = await search_vector(
        conn, await embedder.embed_query(query), embedder.model, filters, limit=candidates
    )
    vector_ids = {hit.listing_id for hit in vector}
    keyword = [hit for hit in keyword if hit.text_match or hit.listing_id in vector_ids]

    keyword_ranks = {hit.listing_id: rank for rank, hit in enumerate(keyword, start=1)}
    vector_ranks = {hit.listing_id: rank for rank, hit in enumerate(vector, start=1)}
    # Title, slug and type are the same in both lists; take them from whichever has the id.
    details = {hit.listing_id: hit for hit in vector} | {hit.listing_id: hit for hit in keyword}
    fused = reciprocal_rank_fusion(
        [[hit.listing_id for hit in keyword], [hit.listing_id for hit in vector]]
    )
    return [
        HybridHit(
            listing_id=listing_id,
            title=details[listing_id].title,
            slug=details[listing_id].slug,
            product_type=details[listing_id].product_type,
            score=score,
            keyword_rank=keyword_ranks.get(listing_id),
            vector_rank=vector_ranks.get(listing_id),
        )
        for listing_id, score in fused[:limit]
    ]
