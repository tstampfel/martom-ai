"""The real Postgres and Redis checks behind GET /health."""

import pytest

from martom.api.health import check_postgres, check_redis

# The anyio pytest plugin (an explicit dev dependency) runs `async def` tests on asyncio.
pytestmark = pytest.mark.anyio

# Port 1 is reserved and never has a server listening, so connecting fails fast.
UNREACHABLE_POSTGRES = "postgresql://martom:martom@127.0.0.1:1/martom"
UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_postgres_check_passes_against_a_running_server(database_url: str) -> None:
    await check_postgres(database_url)


async def test_postgres_check_raises_when_the_server_is_unreachable() -> None:
    with pytest.raises(Exception):  # noqa: B017 - any connection error counts as "down"
        await check_postgres(UNREACHABLE_POSTGRES)


async def test_redis_check_passes_against_a_running_server(redis_url: str) -> None:
    await check_redis(redis_url)


async def test_redis_check_raises_when_the_server_is_unreachable() -> None:
    with pytest.raises(Exception):  # noqa: B017 - any connection error counts as "down"
        await check_redis(UNREACHABLE_REDIS)
