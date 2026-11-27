"""retrieve() (M3-4): parse filters -> hybrid search top 30 -> rerank, against a real Postgres.

The catalogue is the shared search one (tests/search_catalogue.py). The query parser,
embeddings and reranker are fakes: no network call.
"""

import asyncio
import logging
import unicodedata
from collections.abc import AsyncIterator, Sequence

import psycopg
import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from martom.embed_job import run_embedding
from martom.embeddings import EMBEDDING_DIMENSIONS, Embedder
from martom.evals.retrieval import EvalQuestion
from martom.evals.retrieve_eval import run_retrieve_eval
from martom.search.hybrid import search_hybrid
from martom.search.keyword import SearchFilters
from martom.search.query_parser import ParsedQuery, QueryParser
from martom.search.rerank import RerankScore
from martom.search.retrieve import retrieve
from search_catalogue import BRUSILICA, PANELS, T_POST, build_catalogue

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


async def test_parsed_filters_narrow_the_search(
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
    assert T_POST in ids(result.hits)
    assert BRUSILICA not in ids(result.hits)  # no height to read on a grinder


async def test_filters_that_match_nothing_are_dropped(
    conn: psycopg.AsyncConnection, embedder: Embedder
) -> None:
    parser = parser_replying(
        ParsedQuery(height=None, colour="ljubičasta", min_price=None, max_price=None)
    )
    result = await retrieve(conn, "panelna ograda", embedder=embedder, parser=parser, reranker=None)
    assert result.filters_dropped
    assert result.filters == SearchFilters()
    assert set(ids(result.hits)[:2]) == PANELS


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
