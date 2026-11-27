"""retrieve() (M3-4): parse filters -> hybrid search top 30 -> rerank, against a real Postgres.

The catalogue is the shared search one (tests/search_catalogue.py). The query parser,
embeddings and reranker are fakes: no network call.
"""

import asyncio
import logging
import unicodedata
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from decimal import Decimal

import psycopg
import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from martom.embed_job import run_embedding
from martom.embeddings import EMBEDDING_DIMENSIONS, Embedder
from martom.evals.retrieval import EvalQuestion
from martom.evals.retrieve_eval import run_retrieve_eval
from martom.extract.store import save_products
from martom.search.hybrid import search_hybrid
from martom.search.keyword import SearchFilters
from martom.search.query_parser import ParsedQuery, QueryParser
from martom.search.rerank import RerankScore
from martom.search.retrieve import retrieve
from search_catalogue import (
    BRUSILICA,
    PANELS,
    T_POST,
    add_listing,
    build_catalogue,
    synthetic,
)

pytestmark = pytest.mark.anyio

MODEL = "test/concepts"
CONCEPTS = {
    "panel": ("panel",),
    "ograda": ("ograd",),
    "brusilica": ("brusilic",),
    "stup": ("stup", "stub"),
    "kokosi": ("kok",),
}


class ConceptEmbeddings(Embeddings):
    """A text points along every concept it mentions (plus a small bias: no zero vectors)."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.fail = False

    @staticmethod
    def _vector(text: str) -> list[float]:
        plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for axis, stems in enumerate(CONCEPTS.values()):
            if any(stem in plain for stem in stems):
                vector[axis] = 1.0
        vector[-1] = 0.1
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    async def aembed_query(self, text: str) -> list[float]:
        self.started.set()
        if self.fail:
            raise RuntimeError("embedding provider down")
        return self._vector(text)


class FakeReranker:
    """Scores a document by whether it contains `favourite`; records what it was sent."""

    model = "test/reranker"

    def __init__(self, favourite: str = "", error: Exception | None = None) -> None:
        self.favourite = favourite.lower()
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []
        self.delay = 0.0

    async def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        self.calls.append((query, list(documents)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        scores = [
            RerankScore(i, 0.9 if self.favourite and self.favourite in d.lower() else 0.1)
            for i, d in enumerate(documents)
        ]
        return sorted(scores, key=lambda s: -s.relevance)


def parser_replying(reply: ParsedQuery) -> QueryParser:
    async def run(messages: Sequence[BaseMessage]) -> ParsedQuery:
        return reply

    return QueryParser(RunnableLambda(run), model_name="fake")


NO_FILTERS = ParsedQuery(height=None, colour=None, min_price=None, max_price=None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def embeddings() -> ConceptEmbeddings:
    return ConceptEmbeddings()


@pytest.fixture
def embedder(embeddings: ConceptEmbeddings) -> Embedder:
    return Embedder(model=MODEL, embeddings=embeddings)


@pytest.fixture
async def conn(database_url: str, embedder: Embedder) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await build_catalogue(database_url) as c:
        await run_embedding(c, embedder)
        yield c


def ids(hits: list) -> list[int]:
    return [hit.listing_id for hit in hits]


async def test_without_a_rerank_every_filtered_hit_comes_before_every_top_up_hit(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    parser = parser_replying(
        ParsedQuery(height="150 cm", colour=None, min_price=None, max_price=None)
    )
    result = await retrieve(
        conn, "stup za ogradu 150 cm", embedder=embedder, parser=parser, reranker=None
    )
    assert result.parsed
    assert result.filters == SearchFilters(height_cm=150.0)
    assert not result.filters_dropped
    assert result.top_up_added > 0
    matched = {hit.listing_id: hit.matched_filters for hit in result.hits}
    assert matched[T_POST] is True  # a 150 cm variant
    assert False in matched.values()  # e.g. the grinder: no height to read, top-up only
    flags = [hit.matched_filters for hit in result.hits]
    assert flags == sorted(flags, reverse=True)  # all True first, then all False


SINGLE_T_POST = 900301  # sells one thing: no variants, the height only in its title
CHEAP_T_POST = 900302  # the same, 15 KM on the listing
DEAR_T_POST = 900303  # the same, 50 KM on the listing


async def _add_single_t_post(
    conn: psycopg.AsyncConnection,
    embedder: Embedder,
    listing_id: int = SINGLE_T_POST,
    price: float | None = None,
) -> None:
    await add_listing(conn, listing_id, "T STUP 150 CM", f"t-stup-150-cm-{listing_id}", price=price)
    record = replace(synthetic(listing_id, "t stup", "T STUP 150 CM | t stup"), variants=[])
    await save_products(conn, [record])
    await run_embedding(conn, embedder)


@pytest.mark.parametrize("candidates", [30, 1])
async def test_a_parsed_height_never_hides_a_listing_without_variants(
    conn: psycopg.AsyncConnection, embedder: Embedder, candidates: int
) -> None:
    # R1-1: the height filter can't match a listing with no variants ("no height to
    # read"), so the filtered search never returns "T STUP 150 CM". The top-up must still
    # bring it to the reranker, also when the filtered list is already full
    # (candidates=1: T_POST alone fills it). It is marked as not matching the filters.
    await _add_single_t_post(conn, embedder)
    parser = parser_replying(
        ParsedQuery(height="150 cm", colour=None, min_price=None, max_price=None)
    )
    reranker = FakeReranker(favourite="T STUP 150 CM")
    result = await retrieve(
        conn,
        "T stup za ogradu 150 cm",
        embedder=embedder,
        parser=parser,
        reranker=reranker,
        candidates=candidates,
    )
    assert result.filters == SearchFilters(height_cm=150.0)
    assert result.top_up_added > 0
    assert result.hits[0].listing_id == SINGLE_T_POST
    matched = {hit.listing_id: hit.matched_filters for hit in result.hits}
    assert matched[SINGLE_T_POST] is False
    assert matched[T_POST] is True


async def test_the_top_up_never_breaks_a_stated_price_limit(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # R2-1: the reranker reads no price, so an over-budget listing it likes would take a
    # top-10 place. The top-up keeps the price limits and relaxes only height and colour.
    await _add_single_t_post(conn, embedder, CHEAP_T_POST, price=15)
    await _add_single_t_post(conn, embedder, DEAR_T_POST, price=50)
    parser = parser_replying(
        ParsedQuery(height="150 cm", colour=None, min_price=None, max_price="20 KM")
    )
    reranker = FakeReranker(favourite="T STUP 150 CM")
    result = await retrieve(
        conn, "T stup 150 cm do 20 KM", embedder=embedder, parser=parser, reranker=reranker
    )
    assert result.filters == SearchFilters(height_cm=150.0, max_price=Decimal(20))
    found = ids(result.hits)
    assert DEAR_T_POST not in found
    assert CHEAP_T_POST in found  # within budget; only its height couldn't be read
    matched = {hit.listing_id: hit.matched_filters for hit in result.hits}
    assert matched[CHEAP_T_POST] is False
    assert matched[T_POST] is True  # H-150 at 12,25 KM


async def test_a_price_limit_nothing_meets_returns_nothing(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # Over-budget products are never offered as a fallback; "nothing fits" is M4's call.
    parser = parser_replying(
        ParsedQuery(height="153 cm", colour=None, min_price="5000 KM", max_price=None)
    )
    result = await retrieve(conn, "panelna ograda", embedder=embedder, parser=parser, reranker=None)
    assert result.hits == []


async def test_without_filters_every_hit_matches_and_there_is_no_top_up(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    result = await retrieve(conn, "panelna ograda", embedder=embedder, parser=None, reranker=None)
    assert result.top_up_added == 0
    assert result.hits and all(hit.matched_filters for hit in result.hits)


async def test_a_colour_nothing_has_is_relaxed_and_marked(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    parser = parser_replying(
        ParsedQuery(height=None, colour="ljubičasta", min_price=None, max_price=None)
    )
    result = await retrieve(conn, "panelna ograda", embedder=embedder, parser=parser, reranker=None)
    assert result.filters_dropped  # no hit passed every parsed filter
    assert result.filters == SearchFilters(colour="ljubičasta")  # still what was asked
    assert set(ids(result.hits)[:2]) == PANELS
    assert not any(hit.matched_filters for hit in result.hits)


async def test_without_a_parser_the_search_is_unfiltered(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    result = await retrieve(conn, "panelna ograda", embedder=embedder, parser=None, reranker=None)
    assert not result.parsed
    assert result.filters == SearchFilters()
    assert not result.reranked
    hybrid = await search_hybrid(conn, "panelna ograda", embedder=embedder)
    assert ids(result.hits) == ids(hybrid)
    assert all(hit.relevance is None for hit in result.hits)


async def test_rerank_reorders_the_candidates(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    reranker = FakeReranker(favourite="brusilica")
    result = await retrieve(
        conn, "ograda brusilica", embedder=embedder, parser=None, reranker=reranker
    )
    assert result.reranked
    assert result.hits[0].listing_id == BRUSILICA
    assert result.hits[0].relevance == 0.9
    query, documents = reranker.calls[0]
    assert query == "ograda brusilica"
    # The reranker reads each candidate's search_text (title, type, colours, heights...).
    assert any(doc.startswith("PANELNA OGRADA") for doc in documents)


async def test_rerank_sees_up_to_the_candidates_and_returns_the_limit(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    reranker = FakeReranker()
    result = await retrieve(
        conn, "ograda", embedder=embedder, parser=None, reranker=reranker, limit=2, candidates=5
    )
    assert len(reranker.calls[0][1]) == 5
    assert len(result.hits) == 2


@pytest.mark.parametrize("error", [RuntimeError("cohere down"), None])
async def test_a_failed_or_slow_rerank_keeps_the_rrf_order(
    conn: psycopg.AsyncConnection,
    embedder: Embedder,
    caplog: pytest.LogCaptureFixture,
    error: Exception | None,
) -> None:
    reranker = FakeReranker(favourite="brusilica", error=error)
    if error is None:
        reranker.delay = 1.0  # slower than the timeout below
    with caplog.at_level(logging.WARNING):
        result = await retrieve(
            conn,
            "panelna ograda",
            embedder=embedder,
            parser=None,
            reranker=reranker,
            rerank_timeout=0.05,
        )
    assert not result.reranked
    hybrid = await search_hybrid(conn, "panelna ograda", embedder=embedder)
    assert ids(result.hits) == ids(hybrid)
    assert "rerank failed" in caplog.text
    assert "panelna ograda" not in caplog.text  # customer text stays out of the logs


async def test_without_fallback_a_failed_rerank_raises(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    reranker = FakeReranker(error=RuntimeError("cohere down"))
    with pytest.raises(RuntimeError):
        await retrieve(
            conn, "ograda", embedder=embedder, parser=None, reranker=reranker, fallback=False
        )


async def test_a_failed_embedding_still_gives_reranked_keyword_results(
    conn: psycopg.AsyncConnection, embedder: Embedder, embeddings: ConceptEmbeddings
) -> None:
    embeddings.fail = True
    reranker = FakeReranker(favourite="panel")
    result = await retrieve(
        conn, "panelna ograda", embedder=embedder, parser=None, reranker=reranker
    )
    assert result.reranked
    assert set(ids(result.hits)[:2]) == PANELS
    assert all(hit.vector_rank is None for hit in result.hits)


async def test_parsing_and_embedding_run_at_the_same_time(
    conn: psycopg.AsyncConnection, embedder: Embedder, embeddings: ConceptEmbeddings
) -> None:
    async def waits_for_the_embedding(messages: Sequence[BaseMessage]) -> ParsedQuery:
        # Only finishes once the embedding call has started: if retrieve() parsed first and
        # embedded afterwards, this would wait until the parser's timeout and fail.
        await embeddings.started.wait()
        return NO_FILTERS

    parser = QueryParser(RunnableLambda(waits_for_the_embedding), model_name="fake", timeout=1.0)
    result = await retrieve(conn, "panelna ograda", embedder=embedder, parser=parser, reranker=None)
    assert result.parsed


async def test_only_filler_words_return_nothing_and_make_no_paid_call(
    conn: psycopg.AsyncConnection, embedder: Embedder, embeddings: ConceptEmbeddings
) -> None:
    reranker = FakeReranker()
    parse_calls: list = []

    async def run(messages: Sequence[BaseMessage]) -> ParsedQuery:
        parse_calls.append(messages)
        return NO_FILTERS

    parser = QueryParser(RunnableLambda(run), model_name="fake")
    result = await retrieve(conn, "treba mi", embedder=embedder, parser=parser, reranker=reranker)
    assert result.hits == []
    assert reranker.calls == []
    assert parse_calls == []
    assert not embeddings.started.is_set()
    assert set(result.timings_ms) >= {"understand", "search", "rerank", "total"}


async def test_retrieve_eval_scores_every_question_and_keeps_the_details(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    questions = [
        EvalQuestion("q1", "brusilica za metal", (BRUSILICA,), "seed"),
        EvalQuestion("q2", "panelna ograda", tuple(PANELS), "seed"),
    ]
    results, details = await run_retrieve_eval(
        conn, questions, embedder=embedder, parser=None, reranker=FakeReranker("brusilica")
    )
    assert results["q1"][0] == BRUSILICA
    assert set(details) == {"q1", "q2"}
    assert details["q1"].reranked


async def test_retrieve_eval_raises_instead_of_scoring_a_fallback(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    questions = [EvalQuestion("q1", "panelna ograda", tuple(PANELS), "seed")]
    with pytest.raises(RuntimeError):
        await run_retrieve_eval(
            conn,
            questions,
            embedder=embedder,
            parser=None,
            reranker=FakeReranker(error=RuntimeError("cohere down")),
        )


async def test_matched_filters_comes_from_the_data_not_the_rank(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    # R3-1: with candidates=1 only one listing fits the filtered search, so the others that
    # pass the height filter come back through the top-up. They still pass it: the flag
    # must say so.
    parser = parser_replying(
        ParsedQuery(height="153 cm", colour=None, min_price=None, max_price=None)
    )
    result = await retrieve(
        conn, "panelna ograda", embedder=embedder, parser=parser, reranker=None, candidates=1
    )
    passing = {
        hit.listing_id
        for hit in await search_hybrid(
            conn, "panelna ograda", SearchFilters(height_cm=153.0), embedder=embedder, limit=50
        )
    }
    assert len(passing) > 1  # more listings pass than fit in the filtered search
    returned = {hit.listing_id: hit.matched_filters for hit in result.hits}
    assert set(returned) & passing - {result.hits[0].listing_id}  # some came via top-up
    assert returned == {listing_id: listing_id in passing for listing_id in returned}
