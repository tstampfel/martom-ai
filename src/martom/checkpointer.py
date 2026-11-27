"""Postgres checkpointer for the agent graph (LangGraph `langgraph-checkpoint-postgres`).

A checkpointer saves the graph's state after every step, keyed by `thread_id` (one chat),
so a conversation survives an API restart and an interrupted run can resume.

The library creates and versions its own tables (`checkpoints`, `checkpoint_blobs`,
`checkpoint_writes`, `checkpoint_migrations`) with `setup()`. That is the one exception to
"schema changes are SQL files in migrations/": `setup_checkpointer` runs only from
`martom migrate` (the one-off migrate task in production), after our SQL migrations,
never at API startup. The API opens the checkpointer with `open_checkpointer`, which
assumes the tables exist.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool

from martom.migrate import ADVISORY_LOCK_KEY, acquire_advisory_lock

# Connections the API keeps for checkpoint reads and writes. A chat turn holds one only
# while it reads or writes a checkpoint, so a few cover many concurrent chats.
POOL_MAX_SIZE = 5

# What the library needs on every connection: autocommit (its setup() runs
# CREATE INDEX CONCURRENTLY, which can't run inside a transaction), rows as dicts, and no
# server-side prepared statements (they break behind a pooler such as RDS Proxy).
CONNECTION_KWARGS = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}


def setup_checkpointer(database_url: str) -> int:
    """Create or upgrade the checkpointer's tables; returns how many library migrations ran.

    Safe to run again: a second run returns 0. Only `martom migrate` calls this. It holds
    the migration runner's advisory lock, so a second migrate task started at the same
    time waits and then finds nothing to do.
    """
    with psycopg.connect(database_url, **CONNECTION_KWARGS) as conn:
        acquire_advisory_lock(conn)
        try:
            before = _library_version(conn)
            PostgresSaver(conn).setup()
            return _library_version(conn) - before
        finally:
            if not conn.broken:
                conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))


def _library_version(conn: psycopg.Connection) -> int:
    """The library's last applied migration number (its own `checkpoint_migrations.v`).

    -1 when it has never run, so the first setup returns the full migration count.
    """
    with conn.cursor(row_factory=tuple_row) as cur:
        exists = cur.execute("SELECT to_regclass('checkpoint_migrations')").fetchone()
        if exists is None or exists[0] is None:
            return -1
        row = cur.execute("SELECT coalesce(max(v), -1) FROM checkpoint_migrations").fetchone()
    return -1 if row is None else row[0]


@asynccontextmanager
async def open_checkpointer(database_url: str) -> AsyncGenerator[AsyncPostgresSaver]:
    """Yield a checkpointer backed by a small connection pool; closes the pool on exit.

    Pass it to `builder.compile(checkpointer=...)`. Like a `try/finally` around a
    resource in TS: open it once for the app's lifetime (e.g. in FastAPI's lifespan),
    not once per request. The tables must already exist (`martom migrate`).
    """
    # `open=False` + `await pool.open()`: the async pool must be opened inside the event loop.
    # `check`: test each connection before handing it out, so the first chat turn after an
    # RDS restart or an idle disconnect gets a fresh connection instead of a dead one.
    pool = AsyncConnectionPool(
        database_url,
        max_size=POOL_MAX_SIZE,
        kwargs=CONNECTION_KWARGS,
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
    await pool.open()
    try:
        yield AsyncPostgresSaver(pool)
    finally:
        await pool.close()
