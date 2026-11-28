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

# --- The API's Postgres connection budget (all pool sizes live here) ----------------
#
# The instance: db.t4g.micro (infra/variables.tf `db_instance_class`). RDS sets
# max_connections to LEAST({DBInstanceClassMemory/9531392}, 5000) (RDS docs, "Maximum
# number of database connections"). 1 GiB would give 112, but DBInstanceClassMemory
# excludes the memory RDS keeps for the OS, so a micro reports roughly 80-90; we budget
# on the low end, 80. RDS keeps 3 of them for superusers. Re-check these numbers
# (`SHOW max_connections;`) when the instance class or api_desired_count changes.
RDS_MAX_CONNECTIONS = 80
RDS_RESERVED_CONNECTIONS = 3
# api_desired_count = 1 with deployment_maximum_percent = 200 (infra/ecs.tf): during a
# rolling deploy the old and the new task both hold their pools.
API_TASKS_DURING_DEPLOY = 2
# Per API task, besides the pools: /health and /admin open one connection per request.
API_UNPOOLED_CONNECTIONS = 2
# The one-off and scheduled jobs (migrate, sync, extract/embed, a later purge) use one
# connection each, two for migrate (its checkpointer setup); allow three at once.
JOB_CONNECTIONS = 6
# Umami (infra/umami.tf) shares the instance with its own database and role.
# `martom umami-db-setup` (umami_db.py) creates the role with this CONNECTION LIMIT, so
# Postgres refuses a sixth Umami connection (a lost visit count) before it could take one
# from the API. Umami's own pool can't be capped (node-postgres, 10 by default; README,
# "Umami" > "Connections"), and its service never runs two tasks at once
# (deployment_maximum_percent = 100).
UMAMI_CONNECTIONS = 5

# Catalogue reads and searches: each is borrowed for one query or one search, so a few
# cover many concurrent chats.
QUERY_POOL_MAX_SIZE = 8
# Thread turn locks (api/threads.py): a running turn holds one for the whole turn, so
# this caps the turns running at once per task. Never above the query pool: a turn
# borrows at most one query connection at a time, so every running turn can get one.
LOCK_POOL_MAX_SIZE = 8
# The checkpointer (checkpointer.py): held only while a checkpoint is read or written.
CHECKPOINTER_POOL_MAX_SIZE = 4
# Per task 8 + 8 + 4 + 2 = 22; two tasks 44, plus jobs 6 and Umami 5 = 55 of 77 usable (71%).


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
