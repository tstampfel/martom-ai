"""Connection pooling for the API's catalogue queries (#21).

A CLI job uses one connection from start to end. The API serves many chats at once, so
its catalogue, BOM pricer and search borrow a connection from a pool for each query or
search and give it back right after (like a DB client pool in Node). `borrow` accepts
either, so the same class works in a CLI job, a test and the API.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg
from psycopg_pool import AsyncConnectionPool

type Connector = psycopg.AsyncConnection | AsyncConnectionPool

# Connections for catalogue reads and searches; each is borrowed for one query or one
# search, so a few cover many concurrent chats.
QUERY_POOL_MAX_SIZE = 10


@asynccontextmanager
async def borrow(source: Connector) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A connection: the one given, or one borrowed from the pool for this block."""
    if isinstance(source, AsyncConnectionPool):
        async with source.connection() as conn:
            yield conn
    else:
        yield source


def is_autocommit(source: Connector) -> bool:
    if isinstance(source, AsyncConnectionPool):
        return bool((source.kwargs or {}).get("autocommit"))
    return source.autocommit


@asynccontextmanager
async def open_query_pool(
    database_url: str, max_size: int = QUERY_POOL_MAX_SIZE
) -> AsyncGenerator[AsyncConnectionPool]:
    """An autocommit pool, opened inside the event loop and closed on exit.

    Autocommit: a borrowed connection is never left idle in a transaction (search opens
    its own short transactions where it needs one). `check` tests a connection before
    handing it out, so the first chat after an RDS restart gets a live one.
    """
    pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=max_size,
        kwargs={"autocommit": True},
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()
