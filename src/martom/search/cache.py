"""Search cache in Redis (M3-5): a repeated question skips Haiku, the embedding and Cohere.

Three things are cached, each under a key that holds everything that changes its value:

- parsed filters: the parser's model, its PROMPT_VERSION and the question
  (search/query_parser.py). Kept `query_ttl` (QUERY_CACHE_TTL_SECONDS, 24 h).
- the query vector: the embedding model and the question. Also `query_ttl`.
- the whole retrieve() result (ids, titles, slugs, scores; never a price): every model
  and setting retrieve() used, the synonym list, and the catalogue version
  (`catalogue_version`), so a sync, extraction, embed or manual-product change makes every
  cached result a miss at once. Kept `result_ttl` (SEARCH_CACHE_TTL_SECONDS, 1 h), a
  backstop for changes the version can't see (a hand-edited row).

Parsed filters and vectors don't depend on the catalogue, so they outlive a nightly sync:
after one, a repeated question pays only for the search and the rerank.

Privacy: a key never holds the question, only its sha256 (`question_digest`); nothing
here logs a key or a value. The values hold no question text either, except a colour word
the customer wrote, inside the parsed filters.

Redis is optional. Every call has a short socket timeout (REDIS_TIMEOUT_SECONDS) and no
retries, and any error counts as a miss: the search runs uncached and one warning is
logged. After an error, Redis is left alone for RETRY_AFTER_SECONDS, so a dead or
unreachable server costs one timeout per half minute, not one per search. In production
Redis is a sidecar in the API's ECS task, so a deploy starts it empty.
"""

import hashlib
import json
import logging
import struct
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import psycopg
import redis.asyncio as redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from martom.search.keyword import SearchFilters
from martom.search.query_parser import PROMPT_VERSION
from martom.settings import Settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "martom:search"
# Redis runs next to the API (sidecar, or compose locally) and answers in well under a
# millisecond; a call that takes longer than this is treated as Redis being down.
REDIS_TIMEOUT_SECONDS = 0.2
# How long Redis is skipped after an error before it is tried again.
RETRY_AFTER_SECONDS = 30.0


def question_digest(question: str) -> str:
    """sha256 of the question with its spaces collapsed: the only form a key holds."""
    return hashlib.sha256(" ".join(question.split()).encode()).hexdigest()


def redis_client(url: str) -> redis.Redis:
    """A client that fails fast: short timeouts, no retries (redis-py retries 3 times by
    default, which would triple the wait on a dead server)."""
    return redis.Redis.from_url(
        url,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        retry=Retry(NoBackoff(), retries=0),
    )


def filters_to_json(filters: SearchFilters) -> dict[str, Any]:
    return {
        "product_type": filters.product_type,
        "height_cm": filters.height_cm,
        "colour": filters.colour,
        # Decimal as a string: JSON numbers are floats, and 60.50 must stay exact.
        "min_price": None if filters.min_price is None else str(filters.min_price),
        "max_price": None if filters.max_price is None else str(filters.max_price),
    }


def filters_from_json(data: dict[str, Any]) -> SearchFilters:
    return SearchFilters(
        product_type=data["product_type"],
        height_cm=data["height_cm"],
        colour=data["colour"],
        min_price=None if data["min_price"] is None else Decimal(data["min_price"]),
        max_price=None if data["max_price"] is None else Decimal(data["max_price"]),
    )


class SearchCache:
    """Get and set cached search values; a Redis error is a miss, never an exception."""

    def __init__(
        self,
        client: redis.Redis,
        *,
        query_ttl: int,
        result_ttl: int,
        prefix: str = KEY_PREFIX,
        retry_after: float = RETRY_AFTER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.query_ttl = query_ttl
        self.result_ttl = result_ttl
        self.prefix = prefix
        self.retry_after = retry_after
        self.clock = clock
        self._down_until: float | None = None  # set after an error; None = Redis is up

    # -- the three kinds of value -------------------------------------------------------

    def _parse_key(self, parser_model: str, question: str) -> str:
        return f"{self.prefix}:parse:{parser_model}:{PROMPT_VERSION}:{question_digest(question)}"

    def _vector_key(self, embedding_model: str, question: str) -> str:
        return f"{self.prefix}:vector:{embedding_model}:{question_digest(question)}"

    def _result_key(self, key: str) -> str:
        return f"{self.prefix}:result:{key}"

    async def get_filters(self, parser_model: str, question: str) -> SearchFilters | None:
        """The cached parse, or None for a miss (an empty SearchFilters is a hit)."""
        data = await self._get_json(self._parse_key(parser_model, question))
        if data is None:
            return None
        try:
            return filters_from_json(data)
        except (KeyError, TypeError, ArithmeticError):
            return self._unreadable("parse")

    async def set_filters(self, parser_model: str, question: str, filters: SearchFilters) -> None:
        payload = json.dumps(filters_to_json(filters)).encode()
        await self._set(self._parse_key(parser_model, question), payload, self.query_ttl)

    async def get_vector(self, embedding_model: str, question: str) -> list[float] | None:
        raw = await self._get(self._vector_key(embedding_model, question))
        if raw is None:
            return None
        if len(raw) % 8:
            return self._unreadable("vector")
        # Little-endian doubles: exact, and 8 KB for 1024 numbers instead of ~20 KB of JSON.
        return list(struct.unpack(f"<{len(raw) // 8}d", raw))

    async def set_vector(self, embedding_model: str, question: str, vector: list[float]) -> None:
        payload = struct.pack(f"<{len(vector)}d", *vector)
        await self._set(self._vector_key(embedding_model, question), payload, self.query_ttl)

    async def get_result(self, key: str) -> dict[str, Any] | None:
        """A cached retrieve() result as JSON data (retrieve.py turns it back into one)."""
        return await self._get_json(self._result_key(key))

    async def set_result(self, key: str, data: dict[str, Any]) -> None:
        await self._set(self._result_key(key), json.dumps(data).encode(), self.result_ttl)

    async def aclose(self) -> None:
        await self.client.aclose()

    # -- Redis calls, with the fallback ---------------------------------------------------

    async def _get_json(self, key: str) -> Any:
        raw = await self._get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:  # also UnicodeDecodeError
            return self._unreadable("json")

    async def _get(self, key: str) -> bytes | None:
        if not self._available():
            return None
        try:
            value = await self.client.get(key)
        except Exception as exc:
            self._failed(exc)
            return None
        self._recovered()
        return value

    async def _set(self, key: str, value: bytes, ttl: int) -> None:
        if not self._available():
            return
        try:
            await self.client.set(key, value, ex=ttl)
        except Exception as exc:
            self._failed(exc)
            return
        self._recovered()

    def _available(self) -> bool:
        return self._down_until is None or self.clock() >= self._down_until

    def _failed(self, exc: Exception) -> None:
        if self._down_until is None:
            # Logged once per outage, by error type only (never a key or a value).
            logger.warning(
                "search cache unavailable; searching uncached",
                extra={"error": type(exc).__name__, "retry_after_s": self.retry_after},
            )
        self._down_until = self.clock() + self.retry_after

    def _recovered(self) -> None:
        if self._down_until is not None:
            logger.info("search cache available again")
            self._down_until = None

    @staticmethod
    def _unreadable(kind: str) -> None:
        # An entry written by another version of this code, or damaged: treat as a miss.
        logger.warning("unreadable search cache entry; ignored", extra={"kind": kind})
        return None


def make_search_cache(settings: Settings) -> SearchCache | None:
    """The cache for live search, or None when SEARCH_CACHE_ENABLED is false.

    Connecting is lazy: a Redis that is down at startup only makes searches uncached.
    """
    if not settings.search_cache_enabled:
        return None
    return SearchCache(
        redis_client(settings.redis_url),
        query_ttl=settings.query_cache_ttl_seconds,
        result_ttl=settings.search_cache_ttl_seconds,
    )


# What a cached result depends on in the database, in one cheap query:
# - sync_run: a new run (sync or extraction) and each run's end. Syncs add, change and
#   deactivate listings; extraction rewrites products. A run's own writes are covered when
#   it finishes (finished_at), so a result cached during a run lives until it ends.
# - product.embedding_hash: `martom embed` writes no sync_run row; XOR of the hashes'
#   hashtext changes whenever any vector does (order-independent, no sort).
# - manual_product: the TOML entry (content_hash) and its vector.
_CATALOGUE_VERSION_SQL = """
SELECT concat_ws(':',
    (SELECT max(id) FROM sync_run),
    (SELECT extract(epoch FROM max(finished_at)) FROM sync_run),
    (SELECT coalesce(bit_xor(hashtext(embedding_hash)), 0) FROM product),
    (SELECT coalesce(bit_xor(hashtext(content_hash || coalesce(embedding_hash, ''))), 0)
       FROM manual_product))
"""


async def catalogue_version(conn: psycopg.AsyncConnection) -> str:
    """A short string that changes whenever the searchable catalogue does."""
    cursor = await conn.execute(_CATALOGUE_VERSION_SQL)
    row = await cursor.fetchone()
    assert row is not None
    return str(row[0])
