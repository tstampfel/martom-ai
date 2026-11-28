"""Chat thread ids and the per-thread turn lock of POST /api/chat (#21).

**Thread ids.** A thread holds one customer's chat text, and anyone who knows its id
can continue it (and so read it back through the advisor). So the server issues every
id: `secrets.token_urlsafe(32)`, 256 random bits, impossible to guess. The endpoint
continues a thread only when the client sends an id that already has a checkpoint; any
other id (none, malformed, unknown, a client's own invention) starts a new thread with
a fresh id. Logs never carry the id itself, only `thread_log_id` (a short hash), because
a log reader holding the id could continue the chat.

**One turn at a time per thread.** Two requests on one thread at once (a double tap,
two tabs) would both load the same checkpoint and the last writer would drop the other's
turn. Each turn holds a lock for its thread:

- `LocalThreadLocks`: an `asyncio.Lock` per thread id inside one process. Cheap, but a
  second API task doesn't see it.
- `PostgresThreadLocks`: the local lock first (so one process never spends two
  connections on one thread), then a Postgres session advisory lock keyed on a hash of
  the id, taken with `pg_try_advisory_lock` in a polling loop on a connection from its
  own small pool and held for the whole turn. Every API task talks to the same
  database, so this serialises turns across tasks.

Limits: a waiting turn gives up after `timeout` seconds (45 by default, under
CloudFront's 60 s origin read timeout) (`ThreadBusyError`; the endpoint
answers `error` kind `internal`). Each running turn keeps one lock-pool connection, so at
most `LOCK_POOL_MAX_SIZE` turns run at once per task; more wait for a connection within
the same timeout. A task killed mid-turn (OOM, SIGKILL) releases its locks only when
Postgres notices the dropped connection (at once for a closed socket, up to the TCP
keepalive timeout for a vanished host); until then that thread's turns are busy. Two
different threads whose hashes collide (1 in 2^32) just wait for each other.
"""

import asyncio
import hashlib
import logging
import re
import secrets
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

import psycopg
from psycopg_pool import AsyncConnectionPool, PoolTimeout

# Sized with the other pools in martom.db (the connection budget).
from martom.db import LOCK_POOL_MAX_SIZE

logger = logging.getLogger(__name__)

THREAD_ID_BYTES = 32  # token_urlsafe(32) is 43 characters
_THREAD_ID = re.compile(r"[A-Za-z0-9_-]{43}")
# Under CloudFront's 60 s origin read timeout, so `error` + `done` still reach the browser.
TURN_LOCK_TIMEOUT_SECONDS = 45.0
LOCK_POLL_SECONDS = 0.1
# The first of the two int4 keys: a namespace for chat threads. Two-key advisory locks
# never collide with one-key (bigint) locks such as the migration lock.
THREAD_LOCK_CLASS = 0x4D43  # "MC"


class ThreadBusyError(Exception):
    """Another turn on this thread still runs after the wait limit."""


class ThreadLocks(Protocol):
    def hold(self, thread_id: str) -> AbstractAsyncContextManager[None]: ...


def new_thread_id() -> str:
    return secrets.token_urlsafe(THREAD_ID_BYTES)


def is_wellformed_thread_id(value: object) -> bool:
    """True for a string shaped like an id `new_thread_id` issues."""
    return isinstance(value, str) and _THREAD_ID.fullmatch(value) is not None


def thread_log_id(thread_id: str) -> str:
    """A short, stable hash for logs: correlates a thread's lines without revealing its id."""
    return hashlib.sha256(thread_id.encode()).hexdigest()[:12]


def advisory_key(thread_id: str) -> tuple[int, int]:
    """(namespace, signed 32-bit hash of the id) for pg_try_advisory_lock(int4, int4)."""
    digest = hashlib.sha256(thread_id.encode()).digest()
    return THREAD_LOCK_CLASS, int.from_bytes(digest[:4], "big", signed=True)


class LocalThreadLocks:
    """One asyncio.Lock per thread id in this process; unused locks are dropped."""

    def __init__(self, timeout: float = TURN_LOCK_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout
        self._locks: dict[str, asyncio.Lock] = {}
        self._users: dict[str, int] = {}

    @property
    def active_threads(self) -> int:
        return len(self._locks)

    @asynccontextmanager
    async def hold(self, thread_id: str, timeout: float | None = None) -> AsyncGenerator[None]:
        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        self._users[thread_id] = self._users.get(thread_id, 0) + 1
        try:
            try:
                async with asyncio.timeout(self.timeout if timeout is None else timeout):
                    await lock.acquire()
            except TimeoutError:
                raise ThreadBusyError from None
            try:
                yield
            finally:
                lock.release()
        finally:
            self._users[thread_id] -= 1
            if self._users[thread_id] == 0:
                del self._users[thread_id]
                del self._locks[thread_id]


class PostgresThreadLocks:
    """The local lock plus a Postgres advisory lock, so several API tasks agree."""

    def __init__(
        self, pool: AsyncConnectionPool, timeout: float = TURN_LOCK_TIMEOUT_SECONDS
    ) -> None:
        self.pool = pool
        self.timeout = timeout
        self._local = LocalThreadLocks(timeout)

    @asynccontextmanager
    async def hold(self, thread_id: str) -> AsyncGenerator[None]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        async with self._local.hold(thread_id):
            try:
                conn = await self.pool.getconn(timeout=max(deadline - loop.time(), 0.01))
            except PoolTimeout:
                raise ThreadBusyError from None
            try:
                key = advisory_key(thread_id)
                while not await _try_lock(conn, key):
                    if loop.time() >= deadline:
                        raise ThreadBusyError
                    await asyncio.sleep(LOCK_POLL_SECONDS)
                try:
                    yield
                finally:
                    await _unlock(conn, key)
            finally:
                # putconn runs the pool's reset (`_unlock_all`), so a turn cancelled
                # during the unlock above still can't hand a locked connection on.
                await self.pool.putconn(conn)


async def _try_lock(conn: psycopg.AsyncConnection, key: tuple[int, int]) -> bool:
    cursor = await conn.execute("SELECT pg_try_advisory_lock(%s, %s)", key)
    row = await cursor.fetchone()
    return bool(row and row[0])


async def _unlock(conn: psycopg.AsyncConnection, key: tuple[int, int]) -> None:
    """Release the lock; a failure is logged, never raised.

    The turn is finished and checkpointed by now, so an error here must not replace the
    customer's reply with an error (review R1-4). `putconn` then resets the connection
    with `pg_advisory_unlock_all`, or drops a broken one, which ends its session and
    frees the lock either way.
    """
    if conn.broken:
        return
    try:
        await conn.execute("SELECT pg_advisory_unlock(%s, %s)", key)
    except psycopg.Error as exc:
        logger.warning("thread unlock failed", extra={"error": type(exc).__name__})


async def _unlock_all(conn: psycopg.AsyncConnection) -> None:
    await conn.execute("SELECT pg_advisory_unlock_all()")


@asynccontextmanager
async def open_lock_pool(
    database_url: str, max_size: int = LOCK_POOL_MAX_SIZE
) -> AsyncGenerator[AsyncConnectionPool]:
    """The turn locks' own pool: a turn keeps a connection for its whole length, so
    sharing the query pool could leave no connection for the turn's own searches."""
    pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=max_size,
        kwargs={"autocommit": True},
        check=AsyncConnectionPool.check_connection,
        reset=_unlock_all,
        open=False,
    )
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()
