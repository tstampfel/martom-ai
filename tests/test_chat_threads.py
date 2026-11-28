"""Thread ids and the per-thread turn lock of POST /api/chat (#21)."""

import asyncio
import logging

import psycopg
import pytest

from martom.api.threads import (
    LOCK_POOL_MAX_SIZE,
    LocalThreadLocks,
    PostgresThreadLocks,
    ThreadBusyError,
    advisory_key,
    is_wellformed_thread_id,
    new_thread_id,
    thread_log_id,
)
from martom.db import QUERY_POOL_MAX_SIZE

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_new_thread_ids_are_long_random_and_wellformed() -> None:
    ids = {new_thread_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(len(thread_id) == 43 and is_wellformed_thread_id(thread_id) for thread_id in ids)


@pytest.mark.parametrize("bad", ["", "abc", "1", "x" * 42, "x" * 44, "a" * 42 + "/", None])
def test_short_odd_or_missing_ids_are_not_wellformed(bad: str | None) -> None:
    assert not is_wellformed_thread_id(bad)


def test_the_log_id_is_a_short_hash_not_the_thread_id() -> None:
    thread_id = new_thread_id()
    log_id = thread_log_id(thread_id)
    assert len(log_id) == 12
    assert log_id not in thread_id
    assert thread_log_id(thread_id) == log_id


def test_the_advisory_key_fits_two_postgres_integers() -> None:
    for _ in range(50):
        class_id, object_id = advisory_key(new_thread_id())
        assert -(2**31) <= object_id < 2**31
        assert class_id == advisory_key(new_thread_id())[0]


async def test_turns_on_one_thread_run_one_after_another() -> None:
    locks = LocalThreadLocks(timeout=5)
    order: list[str] = []

    async def turn(name: str) -> None:
        async with locks.hold("thread-a"):
            order.append(f"{name} start")
            await asyncio.sleep(0.05)
            order.append(f"{name} end")

    await asyncio.gather(turn("first"), turn("second"))
    assert order == ["first start", "first end", "second start", "second end"]


async def test_turns_on_different_threads_run_at_the_same_time() -> None:
    locks = LocalThreadLocks(timeout=5)
    inside = asyncio.Event()

    async def first() -> None:
        async with locks.hold("thread-a"):
            await asyncio.wait_for(inside.wait(), 1)

    async def second() -> None:
        async with locks.hold("thread-b"):
            inside.set()

    await asyncio.gather(first(), second())


async def test_a_turn_that_waits_too_long_is_busy() -> None:
    locks = LocalThreadLocks(timeout=0.05)
    release = asyncio.Event()

    async def holder() -> None:
        async with locks.hold("thread-a"):
            await release.wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    with pytest.raises(ThreadBusyError):
        async with locks.hold("thread-a"):
            pass
    release.set()
    await task


async def test_the_lock_table_forgets_finished_threads() -> None:
    locks = LocalThreadLocks(timeout=5)
    async with locks.hold("thread-a"):
        pass
    assert locks.active_threads == 0


class FakeConnection:
    """Grants every lock; the unlock fails, like a lock connection that died mid-turn."""

    broken = False

    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, sql: str, params: object = None) -> "FakeConnection":
        self.sql.append(sql)
        if "pg_advisory_unlock(" in sql:
            raise psycopg.OperationalError("connection lost")
        return self

    async def fetchone(self) -> tuple[bool]:
        return (True,)


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConnection()
        self.returned: list[FakeConnection] = []

    async def getconn(self, timeout: float | None = None) -> FakeConnection:
        return self.conn

    async def putconn(self, conn: FakeConnection) -> None:
        self.returned.append(conn)


async def test_a_failed_unlock_never_fails_the_finished_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = FakePool()
    locks = PostgresThreadLocks(pool, timeout=1)  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="martom.api.threads"):
        async with locks.hold("thread-a"):
            pass  # the turn's work; it must not see the unlock error
    # The connection still goes back, so the pool's reset (unlock all) runs.
    assert pool.returned == [pool.conn]
    assert any("pg_advisory_unlock(" in sql for sql in pool.conn.sql)
    assert "thread unlock failed" in caplog.text


def test_every_running_turn_can_get_a_query_connection() -> None:
    # A turn borrows at most one query connection at a time (review R1-8).
    assert LOCK_POOL_MAX_SIZE <= QUERY_POOL_MAX_SIZE
