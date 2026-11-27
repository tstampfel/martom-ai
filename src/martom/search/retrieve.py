"""Retrieval (M3-4): the question -> filters -> hybrid search top 30 -> rerank -> top 10.

This is the "Retrieve" node of the agent graph (docs/PLAN.md, Agent graph):

1. Understand, two calls at the same time (asyncio.gather, like Promise.all): Haiku reads
   the question for filters (search/query_parser.py) while the embedding provider embeds
   it. Neither needs the other, so the slower one sets the time, not their sum.
2. Search: hybrid search (search/hybrid.py) with those filters returns its best
   `candidates` (30) in RRF order. Parsed filters narrow, but must never hide a strong
   match: a height filter can't match a listing that sells one thing without variants
   ("T STUP 150 CM" keeps its height in the title), and a colour filter misses a listing
   whose colour wasn't extracted. So when filters are set, the search also runs without
   them (the query vector is reused: one more SQL query, no paid call), and the
   unfiltered hits the filtered list lacks are added after it: enough to fill it up to
   `candidates`, and at least UNFILTERED_TOP_UP even when it is full
   (`Retrieval.unfiltered_added`). If the filters match nothing at all they are dropped
   (`Retrieval.filters_dropped`).
3. Rerank: Cohere scores the candidates' search_text against the question
   (search/rerank.py) and the best `limit` are returned with their relevance. The
   filtered hits come first, so without a rerank the filters still decide the order.

Every outside call has a fallback, so a customer's search never fails because a provider is
down or slow: no filters if Haiku fails, keyword results only if the embedding fails
(search_hybrid's own fallback), RRF order if the rerank fails or takes longer than
RERANK_TIMEOUT_SECONDS. Evals pass `fallback=False`: no time limits, and any failure is
raised, so a degraded answer is never scored as the real one.

What this does not decide: whether the results are good enough. Hybrid search always
returns products (vector search has no relevance floor, #15), so "nothing relevant" is for
the grading step (M4) to judge, with the rerank `relevance` scores as evidence.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field

import psycopg
from langchain_core.embeddings import Embeddings

from martom.embeddings import Embedder
from martom.search.hybrid import (
    DEFAULT_CANDIDATES,
    QUERY_EMBED_TIMEOUT_SECONDS,
    HybridHit,
    search_hybrid,
)
from martom.search.keyword import LISTING_URL, SearchFilters, strip_filler_words
from martom.search.query_parser import QueryParser
from martom.search.rerank import Reranker

logger = logging.getLogger(__name__)

# The most a search waits for the reranker before answering in RRF order. Rerank 4 Fast on
# 30 short texts usually takes a few hundred milliseconds.
RERANK_TIMEOUT_SECONDS = 1.5
# Unfiltered hits added after a full filtered list (see the module docstring). With 30
# filtered candidates the reranker reads at most 40 texts: still one Cohere search unit
# (up to 100 documents), and a strong match the filter can't see is almost always in the
# unfiltered top 10.
UNFILTERED_TOP_UP = 10


@dataclass(frozen=True)
class RetrievedHit:
    listing_id: int
    title: str
    slug: str | None
    product_type: str
    rrf_score: float
    relevance: float | None  # the reranker's 0..1 score; None when results weren't reranked
    keyword_rank: int | None
    vector_rank: int | None

    @property
    def url(self) -> str | None:
        return LISTING_URL.format(slug=self.slug) if self.slug else None


@dataclass(frozen=True)
class Retrieval:
    hits: list[RetrievedHit]
    filters: SearchFilters  # the filters the returned search ran with
    parsed: bool  # the query parser answered (False: none configured, failed or too slow)
    filters_dropped: bool  # the parsed filters matched nothing, so they were left out
    unfiltered_added: int  # candidates added from the search without filters (R1-1)
    reranked: bool  # False: no reranker configured, it failed, or it was too slow
    timings_ms: dict[str, float] = field(default_factory=dict)


class _KnownQueryVector(Embeddings):
    """Hands search_hybrid the query vector retrieve() already has (or the error it got).

    search_hybrid embeds the query itself; this lets the embedding run next to the parse
    without changing search_hybrid. An error is raised again, so search_hybrid's own
    fallback (keyword results, one logged warning) handles it exactly as before.
    """

    def __init__(self, vector: list[float] | None, error: BaseException | None) -> None:
        self.vector = vector
        self.error = error

    def embed_query(self, text: str) -> list[float]:
        if self.error is not None:
            raise self.error
        assert self.vector is not None
        return self.vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("only the query vector is known")

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


async def _embed(
    embedder: Embedder, question: str, *, fallback: bool
) -> tuple[list[float] | None, BaseException | None]:
    if not fallback:
        return await embedder.embed_query(question), None
    try:
        vector = await asyncio.wait_for(
            embedder.embed_query(question), timeout=QUERY_EMBED_TIMEOUT_SECONDS
        )
    except Exception as exc:
        return None, exc
    return vector, None


async def _no_filters() -> None:
    return None


async def retrieve(
    conn: psycopg.AsyncConnection,
    question: str,
    *,
    embedder: Embedder,
    parser: QueryParser | None,
    reranker: Reranker | None,
    limit: int = 10,
    candidates: int = DEFAULT_CANDIDATES,
    fallback: bool = True,
    rerank_timeout: float = RERANK_TIMEOUT_SECONDS,
) -> Retrieval:
    """The best `limit` active products for a customer's question (see the module docstring)."""
    started = time.perf_counter()
    timings: dict[str, float] = {}

    def lap(name: str, since: float) -> float:
        now = time.perf_counter()
        timings[name] = round((now - since) * 1000, 1)
        return now

    if not strip_filler_words(question):
        # Nothing to search for (search_hybrid would return []): skip the paid calls.
        for name in ("understand", "search", "rerank", "total"):
            timings[name] = 0.0
        return Retrieval([], SearchFilters(), False, False, False, 0, timings)

    parse: Awaitable[SearchFilters | None] = (
        parser.filters(question, fallback=fallback) if parser else _no_filters()
    )
    parsed_filters, (vector, embed_error) = await asyncio.gather(
        parse, _embed(embedder, question, fallback=fallback)
    )
    mark = lap("understand", started)

    known = Embedder(model=embedder.model, embeddings=_KnownQueryVector(vector, embed_error))
    filters = parsed_filters or SearchFilters()
    hybrid = await search_hybrid(
        conn, question, filters, embedder=known, limit=candidates, fallback=fallback
    )
    filters_dropped = False
    unfiltered_added = 0
    if filters != SearchFilters():
        # Room for the top-up beyond hits the filtered list already has.
        unfiltered = await search_hybrid(
            conn,
            question,
            SearchFilters(),
            embedder=known,
            limit=candidates + UNFILTERED_TOP_UP,
            fallback=fallback,
        )
        if not hybrid:
            logger.info("parsed filters matched nothing; searching without them")
            filters, filters_dropped = SearchFilters(), True
            hybrid = unfiltered[:candidates]
        else:
            hybrid, unfiltered_added = _top_up(hybrid, unfiltered, candidates)
    mark = lap("search", mark)

    scores = await _rerank(conn, question, hybrid, reranker, rerank_timeout, fallback=fallback)
    lap("rerank", mark)
    if scores is None:
        hits = [_hit(h, None) for h in hybrid[:limit]]
    else:
        hits = [_hit(hybrid[index], relevance) for index, relevance in scores[:limit]]
    lap("total", started)
    return Retrieval(
        hits=hits,
        filters=filters,
        parsed=parsed_filters is not None,
        filters_dropped=filters_dropped,
        unfiltered_added=unfiltered_added,
        reranked=scores is not None,
        timings_ms=timings,
    )


def _top_up(
    filtered: list[HybridHit], unfiltered: list[HybridHit], candidates: int
) -> tuple[list[HybridHit], int]:
    """The filtered hits, then unfiltered ones they lack: up to `candidates` in all, and at
    least UNFILTERED_TOP_UP of them (when there are that many)."""
    seen = {hit.listing_id for hit in filtered}
    missing = [hit for hit in unfiltered if hit.listing_id not in seen]
    added = missing[: max(candidates - len(filtered), UNFILTERED_TOP_UP)]
    return filtered + added, len(added)


def _hit(hit: HybridHit, relevance: float | None) -> RetrievedHit:
    return RetrievedHit(
        listing_id=hit.listing_id,
        title=hit.title,
        slug=hit.slug,
        product_type=hit.product_type,
        rrf_score=hit.score,
        relevance=relevance,
        keyword_rank=hit.keyword_rank,
        vector_rank=hit.vector_rank,
    )


async def _rerank(
    conn: psycopg.AsyncConnection,
    question: str,
    hits: list[HybridHit],
    reranker: Reranker | None,
    timeout: float,
    *,
    fallback: bool,
) -> list[tuple[int, float]] | None:
    """(index into `hits`, relevance) pairs, best first; None means keep the RRF order."""
    if reranker is None or not hits:
        return None
    documents = await _search_texts(conn, hits)
    if not fallback:
        scores = await reranker.rerank(question, documents)
    else:
        try:
            scores = await asyncio.wait_for(reranker.rerank(question, documents), timeout)
        except Exception as exc:
            # Provider error, rate limit, a malformed answer or the timeout. Logged by type
            # only: the question is customer text.
            logger.warning(
                "rerank failed; results stay in RRF order",
                extra={"rerank_model": reranker.model, "error": type(exc).__name__},
            )
            return None
    return [(s.index, s.relevance) for s in scores]


async def _search_texts(conn: psycopg.AsyncConnection, hits: list[HybridHit]) -> list[str]:
    """Each hit's product.search_text (title, type, brand, colours, heights, uses...).

    The same compact text keyword search indexes; the title alone when it is missing.
    """
    cursor = await conn.execute(
        "SELECT listing_id, search_text FROM product WHERE listing_id = ANY(%s)",
        ([h.listing_id for h in hits],),
    )
    texts = {row[0]: row[1] for row in await cursor.fetchall()}
    return [texts.get(h.listing_id) or h.title for h in hits]
