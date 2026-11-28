"""The Redis search cache (M3-5): hit, miss, Redis down, and what may never be stored.

Hit and miss run against the compose Redis (TEST_REDIS_URL; skipped locally when it is
down, failing in CI), each test under its own random key prefix, deleted afterwards.
Redis-down tests point at a port nothing listens on.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import redis.asyncio as redis
from pydantic import SecretStr

from martom.bom.manual_products import load_manual_products
from martom.search import cache as cache_module
from martom.search.cache import (
    RETRY_AFTER_SECONDS,
    SearchCache,
    catalogue_version,
    make_search_cache,
    question_digest,
    redis_client,
)
from martom.search.keyword import SearchFilters
from martom.search.manual import sync_manual_products
from martom.settings import Settings
from search_catalogue import build_catalogue

pytestmark = pytest.mark.anyio

UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"
QUESTION = "Panelna ograda 1,5 m antracit, imate li?"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def cache(redis_url: str) -> AsyncIterator[SearchCache]:
    prefix = f"test-{uuid.uuid4().hex}"
    cache = SearchCache(redis_client(redis_url), query_ttl=60, result_ttl=60, prefix=prefix)
    yield cache
    keys = [key async for key in cache.client.scan_iter(match=f"{prefix}:*")]
    if keys:
        await cache.client.delete(*keys)
    await cache.aclose()


async def stored_keys(cache: SearchCache) -> list[str]:
    return [key.decode() async for key in cache.client.scan_iter(match=f"{cache.prefix}:*")]


def test_the_digest_ignores_extra_spaces_but_not_words() -> None:
    assert question_digest("  ograda  1,5 m ") == question_digest("ograda 1,5 m")
    assert question_digest("ograda 1,5 m") != question_digest("ograda 1,2 m")
    assert "ograda" not in question_digest("ograda")


async def test_a_miss_returns_none(cache: SearchCache) -> None:
    assert await cache.get_filters("haiku", QUESTION) is None
    assert await cache.get_vector("cohere/x", QUESTION) is None
    assert await cache.get_result("some-key") is None


async def test_filters_come_back_exactly_as_stored(cache: SearchCache) -> None:
    filters = SearchFilters(height_cm=150.0, colour="antracit", max_price=Decimal("60.50"))
    await cache.set_filters("haiku", QUESTION, filters)
    assert await cache.get_filters("haiku", QUESTION) == filters
    # Another parser model (or prompt version) is another key.
    assert await cache.get_filters("sonnet", QUESTION) is None


async def test_no_filters_is_a_hit_not_a_miss(cache: SearchCache) -> None:
    await cache.set_filters("haiku", QUESTION, SearchFilters())
    assert await cache.get_filters("haiku", QUESTION) == SearchFilters()


async def test_a_vector_comes_back_exactly_as_stored(cache: SearchCache) -> None:
    vector = [0.1, -2.5e-7, 1 / 3] + [0.0] * 1021
    await cache.set_vector("cohere/x", QUESTION, vector)
    assert await cache.get_vector("cohere/x", QUESTION) == vector
    assert await cache.get_vector("cohere/y", QUESTION) is None


async def test_a_result_comes_back_as_stored(cache: SearchCache) -> None:
    await cache.set_result("k", {"hits": [{"listing_id": 1, "title": "PANEL"}]})
    assert await cache.get_result("k") == {"hits": [{"listing_id": 1, "title": "PANEL"}]}


async def test_entries_expire(cache: SearchCache) -> None:
    await cache.set_filters("haiku", QUESTION, SearchFilters(colour="zelena"))
    await cache.set_result("k", {"hits": []})
    ttls = {key: await cache.client.ttl(key) for key in await stored_keys(cache)}
    assert len(ttls) == 2
    assert all(0 < ttl <= 60 for ttl in ttls.values())


async def test_the_question_is_never_in_a_key(cache: SearchCache) -> None:
    await cache.set_filters("haiku", QUESTION, SearchFilters())
    await cache.set_vector("cohere/x", QUESTION, [0.5] * 4)
    keys = " ".join(await stored_keys(cache)).lower()
    for word in ("panelna", "ograda", "antracit", "imate"):
        assert word not in keys


async def test_a_corrupt_entry_is_a_miss(cache: SearchCache) -> None:
    await cache.set_filters("haiku", QUESTION, SearchFilters())
    for key in await stored_keys(cache):
        await cache.client.set(key, b"\xff not json")
    assert await cache.get_filters("haiku", QUESTION) is None


async def test_redis_down_is_a_miss_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    cache = SearchCache(redis_client(UNREACHABLE_REDIS), query_ttl=60, result_ttl=60)
    with caplog.at_level(logging.WARNING):
        assert await cache.get_filters("haiku", QUESTION) is None
        await cache.set_vector("cohere/x", QUESTION, [0.5])
        assert await cache.get_result("k") is None
    await cache.aclose()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # One warning per outage, not one per call; never the question.
    assert len(warnings) == 1
    assert "panelna" not in caplog.text.lower()


class CountingRedis(redis.Redis):
    def __init__(self, url: str) -> None:
        pool = redis.ConnectionPool.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2)
        super().__init__(connection_pool=pool)
        self.calls = 0

    async def execute_command(self, *args, **options):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().execute_command(*args, **options)


async def test_after_a_failure_redis_is_skipped_for_a_while() -> None:
    now = [1000.0]
    client = CountingRedis(UNREACHABLE_REDIS)
    cache = SearchCache(client, query_ttl=60, result_ttl=60, retry_after=30, clock=lambda: now[0])
    await cache.get_result("k")
    assert client.calls == 1
    await cache.get_result("k")
    await cache.set_result("k", {})
    assert client.calls == 1  # skipped: no waiting on a dead server for every search
    now[0] += 31
    await cache.get_result("k")
    assert client.calls == 2  # tried again after retry_after
    await cache.aclose()


def test_no_cache_when_turned_off() -> None:
    settings = Settings(search_cache_enabled=False, anthropic_api_key=SecretStr("x"))
    assert make_search_cache(settings) is None


async def test_the_cache_follows_the_settings() -> None:
    settings = Settings(
        redis_url="redis://127.0.0.1:6379/0",
        search_cache_ttl_seconds=120,
        query_cache_ttl_seconds=600,
    )
    cache = make_search_cache(settings)
    assert cache is not None
    assert (cache.result_ttl, cache.query_ttl) == (120, 600)
    await cache.aclose()


async def test_a_vector_of_the_wrong_size_is_a_miss(cache: SearchCache) -> None:
    # R1-1: a damaged vector must not reach search (its size check would make the search
    # fall back to keywords, and that weaker result would then be cached as a full one).
    await cache.set_vector("cohere/x", QUESTION, [0.5] * 4)
    assert await cache.get_vector("cohere/x", QUESTION) is None


@pytest.mark.parametrize(
    "stored",
    [
        b'{"product_type": null, "height_cm": "high", "colour": null,'
        b' "min_price": null, "max_price": null}',
        b'{"product_type": null, "height_cm": null, "colour": 7,'
        b' "min_price": null, "max_price": null}',
        b'{"product_type": null, "height_cm": null, "colour": null,'
        b' "min_price": "lots", "max_price": null}',
        b"[1, 2, 3]",
        b'"just a string"',
    ],
)
async def test_wrong_typed_filters_are_a_miss(cache: SearchCache, stored: bytes) -> None:
    # R1-2: a foreign or damaged entry must never become filters that break the SQL.
    await cache.set_filters("haiku", QUESTION, SearchFilters())
    for key in await stored_keys(cache):
        await cache.client.set(key, stored)
    assert await cache.get_filters("haiku", QUESTION) is None


async def test_a_change_to_the_filter_parsing_code_is_a_new_key(
    cache: SearchCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R1-4: the cached filters are the output of the Python parsing, not only of the prompt.
    await cache.set_filters("haiku", QUESTION, SearchFilters(colour="zelena"))
    monkeypatch.setattr(cache_module, "FILTERS_CACHE_FORMAT", cache_module.FILTERS_CACHE_FORMAT + 1)
    assert await cache.get_filters("haiku", QUESTION) is None


async def test_available_says_whether_redis_is_being_skipped() -> None:
    now = [1000.0]
    cache = SearchCache(
        redis_client(UNREACHABLE_REDIS), query_ttl=60, result_ttl=60, clock=lambda: now[0]
    )
    assert cache.available
    await cache.get_result("k")
    assert not cache.available
    now[0] += RETRY_AFTER_SECONDS + 1
    assert cache.available
    await cache.aclose()


async def test_a_redis_that_never_answers_costs_one_short_timeout() -> None:
    # R1-9: a server that accepts the connection but never replies (hung, overloaded),
    # unlike port 1, which refuses at once.
    async def hang(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(10)

    server = await asyncio.start_server(hang, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cache = SearchCache(redis_client(f"redis://127.0.0.1:{port}/0"), query_ttl=60, result_ttl=60)
    started = time.perf_counter()
    assert await cache.get_filters("haiku", QUESTION) is None
    await cache.set_vector("cohere/x", QUESTION, [0.5] * 1024)  # skipped: breaker is open
    assert await cache.get_result("k") is None
    assert time.perf_counter() - started < 1.0
    await cache.aclose()
    server.close()


async def test_identical_vectors_embedded_together_still_change_the_version(
    database_url: str,
) -> None:
    # R1-3: duplicate listings share a search_text, so a run gives them the same
    # embedding_hash; XOR-ing the bare hashes would cancel the pair out.
    async with await build_catalogue(database_url) as conn:
        await conn.execute("UPDATE product SET embedding_hash = NULL")
        before = await catalogue_version(conn)
        await conn.execute(
            "UPDATE product SET embedding_hash = 'same' WHERE listing_id IN ("
            "SELECT listing_id FROM product ORDER BY listing_id LIMIT 2)"
        )
        assert await catalogue_version(conn) != before


async def test_a_manual_product_change_changes_the_version(database_url: str) -> None:
    # R1-9: a manual_products.toml price change reaches content_hash via migrate/embed.
    async with await build_catalogue(database_url) as conn:
        await sync_manual_products(conn, load_manual_products())
        before = await catalogue_version(conn)
        cursor = await conn.execute("UPDATE manual_product SET content_hash = 'new-price'")
        assert cursor.rowcount > 0
        assert await catalogue_version(conn) != before
