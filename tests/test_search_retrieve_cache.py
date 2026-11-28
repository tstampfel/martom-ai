"""retrieve() with the Redis search cache (M3-5), against a real Postgres and Redis.

A cache hit must skip the paid calls (Haiku parse, query embedding, Cohere rerank); a
catalogue change must turn cached results into misses; evals (fallback=False) must never
read or write the cache; a degraded answer must never be cached; and Redis being down must
change nothing but the speed.
"""

import uuid
from collections.abc import AsyncIterator, Sequence

import psycopg
import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from martom.embed_job import run_embedding
from martom.embeddings import Embedder
from martom.search.cache import SearchCache, redis_client
from martom.search.query_parser import ParsedQuery, QueryParser
from martom.search.retrieve import retrieve
from search_catalogue import build_catalogue
from test_search_retrieve import MODEL, ConceptEmbeddings, FakeReranker

pytestmark = pytest.mark.anyio

QUESTION = "panelna ograda antracit"
UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"


class CountingEmbeddings(ConceptEmbeddings):
    def __init__(self) -> None:
        super().__init__()
        self.query_calls = 0

    async def aembed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return await super().aembed_query(text)


class CountingParser(QueryParser):
    def __init__(self, reply: ParsedQuery) -> None:
        self.calls = 0

        async def run(messages: Sequence[BaseMessage]) -> ParsedQuery:
            self.calls += 1
            return reply

        super().__init__(RunnableLambda(run), model_name="fake-haiku")


COLOUR = ParsedQuery(height=None, colour="antracit", min_price=None, max_price=None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def embeddings() -> CountingEmbeddings:
    return CountingEmbeddings()


@pytest.fixture
def embedder(embeddings: CountingEmbeddings) -> Embedder:
    return Embedder(model=MODEL, embeddings=embeddings)


@pytest.fixture
async def conn(database_url: str, embedder: Embedder) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await build_catalogue(database_url) as c:
        await run_embedding(c, embedder)
        yield c


@pytest.fixture
async def cache(redis_url: str) -> AsyncIterator[SearchCache]:
    prefix = f"test-{uuid.uuid4().hex}"
    cache = SearchCache(redis_client(redis_url), query_ttl=60, result_ttl=60, prefix=prefix)
    yield cache
    keys = [key async for key in cache.client.scan_iter(match=f"{prefix}:*")]
    if keys:
        await cache.client.delete(*keys)
    await cache.aclose()


async def test_a_repeated_question_skips_every_paid_call(
    conn: psycopg.AsyncConnection,
    embedder: Embedder,
    embeddings: CountingEmbeddings,
    cache: SearchCache,
) -> None:
    parser = CountingParser(COLOUR)
    reranker = FakeReranker(favourite="panel")
    first = await retrieve(
        conn, QUESTION, embedder=embedder, parser=parser, reranker=reranker, cache=cache
    )
    assert (parser.calls, embeddings.query_calls, len(reranker.calls)) == (1, 1, 1)
    assert not first.cached

    second = await retrieve(
        conn,
        "  panelna  ograda antracit ",
        embedder=embedder,
        parser=parser,
        reranker=reranker,
        cache=cache,
    )
    assert (parser.calls, embeddings.query_calls, len(reranker.calls)) == (1, 1, 1)
    assert second.cached
    assert second.hits == first.hits
    assert second.filters == first.filters
    assert (second.parsed, second.reranked) == (first.parsed, first.reranked)
    assert second.parse_outcome == first.parse_outcome == "ok"


async def test_a_catalogue_change_misses_the_result_but_reuses_the_parse_and_vector(
    conn: psycopg.AsyncConnection,
    embedder: Embedder,
    embeddings: CountingEmbeddings,
    cache: SearchCache,
) -> None:
    parser = CountingParser(COLOUR)
    reranker = FakeReranker(favourite="panel")
    await retrieve(conn, QUESTION, embedder=embedder, parser=parser, reranker=reranker, cache=cache)
    await conn.execute(
        "INSERT INTO sync_run (kind, status, started_at, finished_at)"
        " VALUES ('extraction', 'succeeded', now(), now())"
    )
    again = await retrieve(
        conn, QUESTION, embedder=embedder, parser=parser, reranker=reranker, cache=cache
    )
    assert not again.cached
    assert len(reranker.calls) == 2  # searched and reranked again on the new catalogue
    assert (parser.calls, embeddings.query_calls) == (1, 1)  # these don't depend on it


async def test_a_new_vector_misses_the_result(
    conn: psycopg.AsyncConnection, embedder: Embedder, cache: SearchCache
) -> None:
    # `martom embed` writes no sync_run row; the vectors' hashes are in the version.
    reranker = FakeReranker()
    await retrieve(conn, QUESTION, embedder=embedder, parser=None, reranker=reranker, cache=cache)
    await conn.execute(
        "UPDATE product SET embedding_hash = 'changed' WHERE listing_id = ("
        "SELECT min(listing_id) FROM product)"
    )
    again = await retrieve(
        conn, QUESTION, embedder=embedder, parser=None, reranker=reranker, cache=cache
    )
    assert not again.cached


async def test_evals_never_read_or_write_the_cache(
    conn: psycopg.AsyncConnection,
    embedder: Embedder,
    embeddings: CountingEmbeddings,
    cache: SearchCache,
) -> None:
    parser = CountingParser(COLOUR)
    for _ in range(2):
        result = await retrieve(
            conn,
            QUESTION,
            embedder=embedder,
            parser=parser,
            reranker=FakeReranker(),
            cache=cache,
            fallback=False,
        )
        assert not result.cached
    assert (parser.calls, embeddings.query_calls) == (2, 2)
    assert [key async for key in cache.client.scan_iter(match=f"{cache.prefix}:*")] == []


async def test_a_degraded_answer_is_never_cached(
    conn: psycopg.AsyncConnection, embedder: Embedder, cache: SearchCache
) -> None:
    broken = FakeReranker(error=RuntimeError("cohere down"))
    first = await retrieve(
        conn, QUESTION, embedder=embedder, parser=None, reranker=broken, cache=cache
    )
    assert first.rerank_outcome == "error"
    working = FakeReranker(favourite="panel")
    second = await retrieve(
        conn, QUESTION, embedder=embedder, parser=None, reranker=working, cache=cache
    )
    assert not second.cached  # the RRF-order fallback wasn't kept for an hour
    assert second.reranked and len(working.calls) == 1


async def test_a_failed_parse_is_never_cached(
    conn: psycopg.AsyncConnection, embedder: Embedder, cache: SearchCache
) -> None:
    calls = 0

    async def broken(messages: Sequence[BaseMessage]) -> ParsedQuery:
        nonlocal calls
        calls += 1
        raise RuntimeError("haiku down")

    parser = QueryParser(RunnableLambda(broken), model_name="fake-haiku")
    for _ in range(2):
        result = await retrieve(
            conn, QUESTION, embedder=embedder, parser=parser, reranker=None, cache=cache
        )
        assert result.parse_outcome == "error"
        assert not result.cached
    assert calls == 2


async def test_redis_down_changes_nothing_but_the_speed(
    conn: psycopg.AsyncConnection, embedder: Embedder, embeddings: CountingEmbeddings
) -> None:
    down = SearchCache(redis_client(UNREACHABLE_REDIS), query_ttl=60, result_ttl=60)
    parser = CountingParser(COLOUR)
    reranker = FakeReranker(favourite="panel")
    without = await retrieve(conn, QUESTION, embedder=embedder, parser=parser, reranker=reranker)
    with_down = await retrieve(
        conn, QUESTION, embedder=embedder, parser=parser, reranker=reranker, cache=down
    )
    await down.aclose()
    assert with_down.hits == without.hits
    assert with_down.filters == without.filters
    assert not with_down.cached
    assert (parser.calls, embeddings.query_calls) == (2, 2)


async def test_another_model_is_another_result(
    conn: psycopg.AsyncConnection, embedder: Embedder, cache: SearchCache
) -> None:
    await retrieve(
        conn, QUESTION, embedder=embedder, parser=None, reranker=FakeReranker(), cache=cache
    )

    class OtherReranker(FakeReranker):
        model = "test/other-reranker"

    other = OtherReranker()
    result = await retrieve(
        conn, QUESTION, embedder=embedder, parser=None, reranker=other, cache=cache
    )
    assert not result.cached and len(other.calls) == 1
