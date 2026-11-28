"""The Postgres turn lock: two API tasks (two lock objects, two pools) share one database."""

import asyncio

import pytest

from martom.api.threads import PostgresThreadLocks, ThreadBusyError, open_lock_pool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_two_tasks_serialise_turns_on_one_thread(database_url: str) -> None:
    order: list[str] = []
    async with open_lock_pool(database_url) as pool_a, open_lock_pool(database_url) as pool_b:
        task_a = PostgresThreadLocks(pool_a, timeout=5)
        task_b = PostgresThreadLocks(pool_b, timeout=5)

        async def turn(locks: PostgresThreadLocks, name: str) -> None:
            async with locks.hold("thread-x"):
                order.append(f"{name} start")
                await asyncio.sleep(0.2)
                order.append(f"{name} end")

        await asyncio.gather(turn(task_a, "a"), turn(task_b, "b"))
    assert order[0].endswith("start") and order[1].endswith("end")
    assert order[1].split()[0] == order[0].split()[0]


async def test_a_turn_waiting_past_the_timeout_is_busy(database_url: str) -> None:
    async with open_lock_pool(database_url) as pool_a, open_lock_pool(database_url) as pool_b:
        holder = PostgresThreadLocks(pool_a, timeout=5)
        waiter = PostgresThreadLocks(pool_b, timeout=0.3)
        async with holder.hold("thread-y"):
            with pytest.raises(ThreadBusyError):
                async with waiter.hold("thread-y"):
                    pass
        # Released on exit: the other task gets it now.
        async with waiter.hold("thread-y"):
            pass


async def test_other_threads_are_not_blocked(database_url: str) -> None:
    async with open_lock_pool(database_url) as pool_a, open_lock_pool(database_url) as pool_b:
        holder = PostgresThreadLocks(pool_a, timeout=5)
        other = PostgresThreadLocks(pool_b, timeout=0.5)
        async with holder.hold("thread-1"), other.hold("thread-2"):
            pass
