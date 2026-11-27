"""Retrieval (M3-4): the question -> filters -> hybrid search top 30 -> rerank -> top 10.

This is the "Retrieve" node of the agent graph (docs/PLAN.md, Agent graph):

1. Understand, two calls at the same time (asyncio.gather, like Promise.all): Haiku reads
   the question for filters (search/query_parser.py) while the embedding provider embeds
   it. Neither needs the other, so the slower one sets the time, not their sum.
2. Search: hybrid search (search/hybrid.py) with those filters returns its best
   `candidates` (30) in RRF order. Parsed filters narrow, but must never hide a strong
   match: a height filter can't match a listing that sells one thing without variants
   ("T STUP 150 CM" keeps its height in the title), and a colour filter misses a listing
   whose colour wasn't extracted. So when a height or colour is set, the search also runs
   with those two relaxed (`relax_filters`; the query vector is reused: one more SQL
   query, no paid call), and the hits the filtered list lacks are added after it: enough
   to fill it up to `candidates`, and at least TOP_UP even when it is full
   (`Retrieval.top_up_added`). Price limits are never relaxed: a price is known data, and
   a listing over the customer's stated limit fails on purpose (R2-1). So nothing over
   budget is ever returned, and a price limit nothing meets returns no hits.
3. Rerank: Cohere scores the candidates' search_text against the question
   (search/rerank.py) and the best `limit` are returned with their relevance, in the
   reranker's order. Without a rerank, the filtered hits come first.

Each hit says whether it passed every parsed filter (`RetrievedHit.matched_filters`),
checked against the data with the shared FILTERS_SQL, not inferred from which search found
it: a top-up hit can pass every filter and only have ranked below the filtered top 30.
The order is left to the reranker on purpose: putting every matched hit first would push
the "T STUP 150 CM" that the top-up exists for back out of the top 10. Whether to offer
an unmatched hit, and how to word it ("height not stated in the listing"), is for the
agent (M4) to decide.

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
from dataclasses import dataclass, field, replace

import psycopg
from langchain_core.embeddings import Embeddings

from martom.embeddings import Embedder
from martom.search.hybrid import (
    DEFAULT_CANDIDATES,
    QUERY_EMBED_TIMEOUT_SECONDS,
    HybridHit,
    search_hybrid,
)
from martom.search.keyword import (
    FILTERS_SQL,
    LISTING_URL,
    SearchFilters,
    filter_params,
    strip_filler_words,
)
from martom.search.query_parser import QueryParser
from martom.search.rerank import Reranker

logger = logging.getLogger(__name__)

# The most a search waits for the reranker before answering in RRF order. Rerank 4 Fast on
# 30 short texts usually takes a few hundred milliseconds.
RERANK_TIMEOUT_SECONDS = 1.5
# Relaxed-search hits added after a full filtered list (see the module docstring). With 30
# filtered candidates the reranker reads at most 40 texts: still one Cohere search unit
# (up to 100 documents), and a strong match the filter can't see is almost always in the
# relaxed top 10.
TOP_UP = 10


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
    # True when the hit passed every parsed filter (always, when none were parsed); False
    # for a hit whose height or colour couldn't be confirmed (checked in the database).
    # Never over a price limit either way.
    matched_filters: bool = True

    @property
    def url(self) -> str | None:
        return LISTING_URL.format(slug=self.slug) if self.slug else None


@dataclass(frozen=True)
class Retrieval:
    hits: list[RetrievedHit]
    filters: SearchFilters  # what the customer asked for (parsed); see matched_filters
    parsed: bool  # the query parser answered (False: none configured, failed or too slow)
    filters_dropped: bool  # no candidate passed every parsed filter; all are top-up hits
    top_up_added: int  # candidates added from the relaxed search (R1-1)
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
        return Retrieval([], SearchFilters(), False, False, 0, False, timings)

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
    matched = {hit.listing_id for hit in hybrid}
    top_up_added = 0
    relaxed = relax_filters(filters)
    if relaxed != filters:
        # Room for the top-up beyond hits the filtered list already has.
        wider = await search_hybrid(
            conn,
            question,
            relaxed,
            embedder=known,
            limit=candidates + TOP_UP,
            fallback=fallback,
        )
        hybrid, top_up_added = _top_up(hybrid, wider, candidates)
        # A top-up hit may still pass every filter: it only ranked below the filtered
        # search's top `candidates` (R3-1). Ask the data, not the rank.
        matched |= await _passing_filters(
            conn, [h.listing_id for h in hybrid if h.listing_id not in matched], filters
        )
    filters_dropped = bool(hybrid) and not matched
    if filters_dropped:
        logger.info("no candidate passed every parsed filter; relaxed height and colour")
    mark = lap("search", mark)

    scores = await _rerank(conn, question, hybrid, reranker, rerank_timeout, fallback=fallback)
    lap("rerank", mark)
    if scores is None:
        picked = [(h, None) for h in hybrid[:limit]]
    else:
        picked = [(hybrid[index], relevance) for index, relevance in scores[:limit]]
    hits = [_hit(h, relevance, h.listing_id in matched) for h, relevance in picked]
    lap("total", started)
    return Retrieval(
        hits=hits,
        filters=filters,
        parsed=parsed_filters is not None,
        filters_dropped=filters_dropped,
        top_up_added=top_up_added,
        reranked=scores is not None,
        timings_ms=timings,
    )


_PASSING_FILTERS_SQL = (
    "SELECT p.listing_id FROM product AS p"
    " JOIN active_olx_listing AS l ON l.id = p.listing_id"
    " WHERE p.listing_id = ANY(%(ids)s) AND" + FILTERS_SQL
)


async def _passing_filters(
    conn: psycopg.AsyncConnection, listing_ids: list[int], filters: SearchFilters
) -> set[int]:
    """The ids among `listing_ids` that pass every filter, in one query on the shared
    FILTERS_SQL, so "passes the filters" has one definition (keyword.py)."""
    if not listing_ids:
        return set()
    cursor = await conn.execute(
        _PASSING_FILTERS_SQL, {"ids": listing_ids, **filter_params(filters)}
    )
    return {row[0] for row in await cursor.fetchall()}


def relax_filters(filters: SearchFilters) -> SearchFilters:
    """The filters the top-up search keeps. Why each one is or isn't relaxed:

    - height: relaxed. A listing that sells one thing has no variants, so no height the
      filter can read ("T STUP 150 CM" has it only in the title): a miss from missing data.
    - colour: relaxed. A colour the extraction didn't copy ("RAL 7016" left in the text) is
      the same kind of miss. The reranker still reads the colours in search_text.
    - min_price / max_price: kept. The price is known, a listing outside the customer's
      stated limit fails on purpose, and the reranker can't see prices (search_text has
      none), so a relaxed price would let an over-budget product into the top 10.
    - product_type: kept (the parser never sets it; a caller that does meant it).
    """
    return replace(filters, height_cm=None, colour=None)


def _top_up(
    filtered: list[HybridHit], wider: list[HybridHit], candidates: int
) -> tuple[list[HybridHit], int]:
    """The filtered hits, then relaxed-search ones they lack: up to `candidates` in all, and
    at least TOP_UP of them (when there are that many)."""
    seen = {hit.listing_id for hit in filtered}
    missing = [hit for hit in wider if hit.listing_id not in seen]
    added = missing[: max(candidates - len(filtered), TOP_UP)]
    return filtered + added, len(added)


def _hit(hit: HybridHit, relevance: float | None, matched_filters: bool) -> RetrievedHit:
    return RetrievedHit(
        listing_id=hit.listing_id,
        title=hit.title,
        slug=hit.slug,
        product_type=hit.product_type,
        rrf_score=hit.score,
        relevance=relevance,
        keyword_rank=hit.keyword_rank,
        vector_rank=hit.vector_rank,
        matched_filters=matched_filters,
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
