"""Retrieval (M3-4): the question -> filters -> hybrid search top 30 -> rerank -> top 10.

This is the "Retrieve" node of the agent graph (docs/PLAN.md, Agent graph):

1. Understand, two jobs at the same time (asyncio.TaskGroup, like Promise.all): Haiku reads
   the question for filters (search/query_parser.py), while the embedding provider embeds
   it and hybrid search runs without filters (the result of a parse fallback, and the
   top-up's source). Neither needs the other, so the slower one sets the time, not their
   sum: the parse limit (2.5 s) costs nothing beyond the parse itself.
2. Search: hybrid search (search/hybrid.py) with those filters returns its best
   `candidates` (30) in RRF order. Parsed filters narrow, but must never hide a strong
   match: a height filter can't match a listing that sells one thing without variants
   ("T STUP 150 CM" keeps its height in the title), and a colour filter misses a listing
   whose colour wasn't extracted. So when a height or colour is set, hits from a search
   with those two relaxed (`relax_filters`: the search from step 1, or with a price limit
   one more SQL query; the query vector is reused, no paid call) that the filtered list
   lacks are added after it: enough
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
RERANK_TIMEOUT_SECONDS. Each search logs one "retrieval finished" line with how the parse
and the rerank went (`parse_outcome`, `rerank_outcome`: ok / timeout / error / skipped, plus
their times), so monitoring (M6-3) can watch the fallback rates; never the question. Evals
pass `fallback=False`: no time limits, and any failure is
raised, so a degraded answer is never scored as the real one.

Manual products (manual_products.toml, #112) come through the same steps: both searches
return them, the filters are checked with MANUAL_FILTERS_SQL, and the reranker reads their
`manual_product.search_text`. A manual hit has `source="manual"`, its TOML key in
`manual_key`, `listing_id=None` and no OLX link; its price is read from the TOML, never here.

What this does not decide: whether the results are good enough. Hybrid search always
returns products (vector search has no relevance floor, #15), so "nothing relevant" is for
the grading step (M4) to judge, with the rerank `relevance` scores as evidence.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import httpx
import psycopg
from langchain_core.embeddings import Embeddings

from martom.embeddings import Embedder
from martom.search import query_parser
from martom.search.cache import (
    SearchCache,
    catalogue_version,
    filters_from_json,
    filters_to_json,
    question_digest,
)
from martom.search.hybrid import (
    DEFAULT_CANDIDATES,
    QUERY_EMBED_TIMEOUT_SECONDS,
    HybridHit,
    search_hybrid,
)
from martom.search.keyword import (
    FILTERS_SQL,
    MANUAL_FILTERS_SQL,
    HitKey,
    SearchFilters,
    Source,
    filter_params,
    hit_key,
    key_from_row,
    listing_url,
    strip_filler_words,
)
from martom.search.query_parser import ParseResult, QueryParser
from martom.search.rerank import Reranker
from martom.search.synonyms import default_query_synonyms

logger = logging.getLogger(__name__)

# The most a search waits for the reranker before answering in RRF order. Rerank 5 Fast on
# 30 short texts usually takes a few hundred milliseconds.
RERANK_TIMEOUT_SECONDS = 1.5
# Relaxed-search hits added after a full filtered list (see the module docstring). With 30
# filtered candidates the reranker reads at most 40 texts: still one Cohere search unit
# (up to 100 documents), and a strong match the filter can't see is almost always in the
# relaxed top 10.
TOP_UP = 10


@dataclass(frozen=True)
class RetrievedHit:
    listing_id: int | None  # None for a manual product (source "manual")
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
    # "olx" for a listing, "manual" for a manual_products.toml entry (#112), which has no
    # listing id and no OLX link; `manual_key` is its TOML key (and BOM item key).
    source: Source = "olx"
    manual_key: str | None = None

    @property
    def key(self) -> HitKey:
        return hit_key(self.source, self.listing_id, self.manual_key)

    @property
    def url(self) -> str | None:
        return listing_url(self.source, self.slug)


@dataclass(frozen=True)
class Retrieval:
    hits: list[RetrievedHit]
    filters: SearchFilters  # what the customer asked for (parsed); see matched_filters
    parsed: bool  # the query parser answered (False: none configured, failed or too slow)
    filters_dropped: bool  # no candidate passed every parsed filter; all are top-up hits
    top_up_added: int  # candidates added from the relaxed search (R1-1)
    reranked: bool  # False: no reranker configured, it failed, or it was too slow
    timings_ms: dict[str, float] = field(default_factory=dict)
    # How the parse and the rerank went (the fallback metric, see the "retrieval" log line):
    # "ok", "timeout", "error", or "skipped" (none configured, or nothing to rerank).
    parse_outcome: str = "skipped"
    rerank_outcome: str = "skipped"
    # True when the whole result came from the search cache (M3-5): no parse, embedding,
    # search or rerank ran; the outcomes above are those of the search that was cached.
    cached: bool = False


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
    embedder: Embedder, question: str, *, fallback: bool, cache: SearchCache | None = None
) -> tuple[list[float] | None, BaseException | None, bool]:
    """(vector, error, whether the vector came from the cache)."""
    if not fallback:
        return await embedder.embed_query(question), None, False
    if cache is not None:
        cached = await cache.get_vector(embedder.model, question)
        if cached is not None:
            return cached, None, True
    try:
        vector = await asyncio.wait_for(
            embedder.embed_query(question), timeout=QUERY_EMBED_TIMEOUT_SECONDS
        )
    except Exception as exc:
        return None, exc, False
    if cache is not None:
        await cache.set_vector(embedder.model, question, vector)
    return vector, None, False


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
    cache: SearchCache | None = None,
) -> Retrieval:
    """The best `limit` active products for a customer's question (see the module docstring).

    With a `cache` (search/cache.py), a repeated question within its TTL and on the same
    catalogue returns the cached result without any paid call, and a parse or query vector
    already cached is reused. Only live search uses it: with `fallback=False` (evals) the
    cache is never read or written, so a score never comes from an earlier run.
    """
    started = time.perf_counter()
    timings: dict[str, float] = {}
    if not fallback:
        cache = None

    def lap(name: str, since: float) -> float:
        now = time.perf_counter()
        timings[name] = round((now - since) * 1000, 1)
        return now

    if not strip_filler_words(question):
        # Nothing to search for (search_hybrid would return []): skip the paid calls.
        for name in ("understand", "parse", "search", "rerank", "total"):
            timings[name] = 0.0
        return Retrieval([], SearchFilters(), False, False, 0, False, timings)

    result_key: str | None = None
    if cache is not None:
        result_key = await _result_key(
            conn, question, embedder, parser, reranker, limit=limit, candidates=candidates
        )
        hit = _retrieval_from_json(await cache.get_result(result_key))
        if hit is not None:
            for name in ("understand", "parse", "search", "rerank"):
                timings[name] = 0.0
            lap("total", started)
            logger.info(
                "retrieval finished",
                extra={
                    "event": "retrieval",
                    "cache": "hit",
                    "total_ms": timings["total"],
                    "hits": len(hit.hits),
                },
            )
            return replace(hit, timings_ms=timings, cached=True)

    cached_parts: set[str] = set()  # "parse" / "embedding" when reused from the cache

    async def parse() -> ParseResult | None:
        if parser is None:
            return None
        begun = time.perf_counter()
        try:
            if cache is not None:
                cached = await cache.get_filters(parser.model_name, question)
                if cached is not None:
                    cached_parts.add("parse")
                    return ParseResult(cached, "ok")
            result = await parser.parse_filters(question, fallback=fallback)
            if cache is not None and result.outcome == "ok" and result.filters is not None:
                await cache.set_filters(parser.model_name, question, result.filters)
            return result
        finally:
            timings["parse"] = round((time.perf_counter() - begun) * 1000, 1)

    async def embed_and_search_unfiltered() -> tuple[Embedder, bool, list[HybridHit]]:
        # Needs no filters, so it runs while Haiku reads the question: the search without
        # filters is what a parse fallback returns, and the top-up's source otherwise.
        vector, embed_error, from_cache = await _embed(
            embedder, question, fallback=fallback, cache=cache
        )
        if from_cache:
            cached_parts.add("embedding")
        known = Embedder(model=embedder.model, embeddings=_KnownQueryVector(vector, embed_error))
        unfiltered = await search_hybrid(
            conn,
            question,
            SearchFilters(),
            embedder=known,
            limit=candidates + TOP_UP,  # room for the top-up beyond the filtered hits
            fallback=fallback,
        )
        return known, embed_error is not None, unfiltered

    timings["parse"] = 0.0
    # A TaskGroup, not gather: if one side raises, the other is cancelled, so no Haiku call
    # (or, in evals, no SQL on `conn`) outlives a failed retrieve().
    try:
        async with asyncio.TaskGroup() as group:
            parsing = group.create_task(parse())
            prefetching = group.create_task(embed_and_search_unfiltered())
    except BaseExceptionGroup as failed:
        # Callers see the original error, as they did with gather, not an ExceptionGroup.
        raise failed.exceptions[0] from None
    parse_result = parsing.result()
    known, embedding_fallback, unfiltered = prefetching.result()
    mark = lap("understand", started)

    parsed_filters = parse_result.filters if parse_result else None
    filters = parsed_filters or SearchFilters()
    top_up_added = 0
    if filters == SearchFilters():
        hybrid = unfiltered[:candidates]
        matched = {hit.key for hit in hybrid}
    else:
        hybrid = await search_hybrid(
            conn, question, filters, embedder=known, limit=candidates, fallback=fallback
        )
        matched = {hit.key for hit in hybrid}
        relaxed = relax_filters(filters)
        if relaxed != filters:
            # Only a price limit (or a type) survives relaxing; without one the prefetched
            # unfiltered search is the relaxed search.
            wider = (
                unfiltered
                if relaxed == SearchFilters()
                else await search_hybrid(
                    conn,
                    question,
                    relaxed,
                    embedder=known,
                    limit=candidates + TOP_UP,
                    fallback=fallback,
                )
            )
            hybrid, top_up_added = _top_up(hybrid, wider, candidates)
            # A top-up hit may still pass every filter: it only ranked below the filtered
            # search's top `candidates` (R3-1). Ask the data, not the rank.
            matched |= await _passing_filters(
                conn, [h for h in hybrid if h.key not in matched], filters
            )
    filters_dropped = bool(hybrid) and not matched
    if filters_dropped:
        logger.info("no candidate passed every parsed filter; relaxed height and colour")
    mark = lap("search", mark)

    scores, rerank_outcome = await _rerank(
        conn, question, hybrid, reranker, rerank_timeout, fallback=fallback
    )
    lap("rerank", mark)
    if scores is None:
        picked = [(h, None) for h in hybrid[:limit]]
    else:
        picked = [(hybrid[index], relevance) for index, relevance in scores[:limit]]
    hits = [_hit(h, relevance, h.key in matched) for h, relevance in picked]
    lap("total", started)
    parse_outcome = parse_result.outcome if parse_result else "skipped"
    # One line per search for monitoring (M6-3): the parse and rerank fallback rates come
    # from parse_outcome / rerank_outcome. Never the question (customer text).
    logger.info(
        "retrieval finished",
        extra={
            "event": "retrieval",
            "parse_outcome": parse_outcome,
            "parse_ms": timings["parse"],
            "rerank_outcome": rerank_outcome,
            "rerank_ms": timings["rerank"],
            "total_ms": timings["total"],
            "embedding_fallback": embedding_fallback,
            "filters_parsed": filters != SearchFilters(),
            "top_up_added": top_up_added,
            "hits": len(hits),
            "cache": "off" if cache is None else "miss",
            "parse_cached": "parse" in cached_parts,
            "embedding_cached": "embedding" in cached_parts,
        },
    )
    retrieval = Retrieval(
        hits=hits,
        filters=filters,
        parsed=parsed_filters is not None,
        filters_dropped=filters_dropped,
        top_up_added=top_up_added,
        reranked=scores is not None,
        timings_ms=timings,
        parse_outcome=parse_outcome,
        rerank_outcome=rerank_outcome,
    )
    # Only a full answer is cached: a fallback (no filters, keyword only, RRF order) would
    # otherwise be served for an hour after the provider is back. "skipped" means no parser
    # or reranker is configured (part of the key) or nothing to rerank: nothing degraded.
    degraded = (
        embedding_fallback
        or parse_outcome not in ("ok", "skipped")
        or rerank_outcome not in ("ok", "skipped")
    )
    if cache is not None and result_key is not None and not degraded:
        await cache.set_result(result_key, _retrieval_to_json(retrieval))
    return retrieval


# Bump when Retrieval, RetrievedHit or how retrieve() ranks changes, so a local Redis that
# outlives the code never serves a result made the old way. (In production Redis is a
# sidecar that every deploy starts empty.)
RESULT_CACHE_FORMAT = 1


async def _result_key(
    conn: psycopg.AsyncConnection,
    question: str,
    embedder: Embedder,
    parser: QueryParser | None,
    reranker: Reranker | None,
    *,
    limit: int,
    candidates: int,
) -> str:
    """sha256 of everything a retrieve() result depends on (never the question in clear)."""
    parts = {
        "format": RESULT_CACHE_FORMAT,
        "question": question_digest(question),
        "parser": None if parser is None else parser.model_name,
        "prompt_version": query_parser.PROMPT_VERSION,
        "embedding_model": embedder.model,
        "rerank_model": None if reranker is None else reranker.model,
        "synonyms": repr(default_query_synonyms().rules),
        "limit": limit,
        "candidates": candidates,
        "catalogue": await catalogue_version(conn),
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def _retrieval_to_json(retrieval: Retrieval) -> dict[str, Any]:
    """The cacheable part of a Retrieval (not its timings): ids, titles, scores, filters.
    No prices: a hit has none (CLAUDE.md, grounding)."""
    return {
        "hits": [asdict(hit) for hit in retrieval.hits],
        "filters": filters_to_json(retrieval.filters),
        "parsed": retrieval.parsed,
        "filters_dropped": retrieval.filters_dropped,
        "top_up_added": retrieval.top_up_added,
        "reranked": retrieval.reranked,
        "parse_outcome": retrieval.parse_outcome,
        "rerank_outcome": retrieval.rerank_outcome,
    }


def _retrieval_from_json(data: dict[str, Any] | None) -> Retrieval | None:
    """A Retrieval back from the cache, or None for a miss or an entry that doesn't fit."""
    if data is None:
        return None
    try:
        return Retrieval(
            hits=[RetrievedHit(**hit) for hit in data["hits"]],
            filters=filters_from_json(data["filters"]),
            parsed=data["parsed"],
            filters_dropped=data["filters_dropped"],
            top_up_added=data["top_up_added"],
            reranked=data["reranked"],
            parse_outcome=data["parse_outcome"],
            rerank_outcome=data["rerank_outcome"],
        )
    except (KeyError, TypeError, ArithmeticError):
        logger.warning("unreadable search cache entry; ignored", extra={"kind": "result"})
        return None


_PASSING_FILTERS_SQL = (
    "SELECT 'olx', p.listing_id::text FROM product AS p"
    " JOIN active_olx_listing AS l ON l.id = p.listing_id"
    " WHERE p.listing_id = ANY(%(ids)s) AND"
    + FILTERS_SQL
    + " UNION ALL SELECT 'manual', p.key FROM manual_product AS p"
    " WHERE p.key = ANY(%(keys)s) AND" + MANUAL_FILTERS_SQL
)


async def _passing_filters(
    conn: psycopg.AsyncConnection, hits: list[HybridHit], filters: SearchFilters
) -> set[HitKey]:
    """The keys of the `hits` that pass every filter, in one query on the shared
    FILTERS_SQL / MANUAL_FILTERS_SQL, so "passes the filters" has one definition
    (keyword.py)."""
    if not hits:
        return set()
    cursor = await conn.execute(
        _PASSING_FILTERS_SQL,
        {
            "ids": [h.listing_id for h in hits if h.source == "olx"],
            "keys": [h.manual_key for h in hits if h.source == "manual"],
            **filter_params(filters),
        },
    )
    return {key_from_row(source, value) for source, value in await cursor.fetchall()}


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
    seen = {hit.key for hit in filtered}
    missing = [hit for hit in wider if hit.key not in seen]
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
        source=hit.source,
        manual_key=hit.manual_key,
    )


async def _rerank(
    conn: psycopg.AsyncConnection,
    question: str,
    hits: list[HybridHit],
    reranker: Reranker | None,
    timeout: float,
    *,
    fallback: bool,
) -> tuple[list[tuple[int, float]] | None, str]:
    """(index into `hits`, relevance) pairs, best first, and the outcome ("ok", "timeout",
    "error" or "skipped"); no pairs means keep the RRF order."""
    if reranker is None or not hits:
        return None, "skipped"
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
            # The client's own timeout (httpx, under the Cohere SDK) counts as a timeout
            # too, like the parser's APITimeoutError.
            timed_out = isinstance(exc, (TimeoutError, httpx.TimeoutException))
            return None, "timeout" if timed_out else "error"
    return [(s.index, s.relevance) for s in scores], "ok"


async def _search_texts(conn: psycopg.AsyncConnection, hits: list[HybridHit]) -> list[str]:
    """Each hit's product.search_text (title, type, brand, colours, heights, uses...), or
    manual_product.search_text for a manual product.

    The same compact text keyword search indexes; the title alone when it is missing.
    """
    cursor = await conn.execute(
        "SELECT 'olx', listing_id::text, search_text FROM product WHERE listing_id = ANY(%(ids)s)"
        " UNION ALL"
        " SELECT 'manual', key, search_text FROM manual_product WHERE key = ANY(%(keys)s)",
        {
            "ids": [h.listing_id for h in hits if h.source == "olx"],
            "keys": [h.manual_key for h in hits if h.source == "manual"],
        },
    )
    texts: dict[HitKey, str] = {
        key_from_row(source, value): text for source, value, text in await cursor.fetchall()
    }
    return [texts.get(h.key) or h.title for h in hits]
