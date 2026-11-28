"""Chat retention: delete customer chats that have been idle longer than the retention.

Decided by Tomislav (#129, 27 Nov 2026): chats are kept 90 days (`CHAT_RETENTION_DAYS`),
then the nightly `martom purge-chats` task deletes them. A chat is one checkpointer
thread; its age is the time of its *last* checkpoint, so a customer who comes back keeps
the whole thread for another 90 days.

The job only ever deletes through the saver's `adelete_thread`, which touches the
library's `checkpoints`, `checkpoint_blobs` and `checkpoint_writes` rows of that thread
and nothing else: orders (`order_request`, kept 12 months) are never affected.

It logs and returns counts only, never thread ids (they are the customers' session
tokens) or chat text.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

from martom.checkpointer import open_checkpointer

logger = logging.getLogger(__name__)

# Every checkpoint carries `ts`, the ISO 8601 time the library wrote it. A thread's age
# is its newest checkpoint across all namespaces (subgraphs write their own).
EXPIRED_THREADS_SQL = """
SELECT thread_id
FROM checkpoints
GROUP BY thread_id
HAVING max((checkpoint ->> 'ts')::timestamptz) < %s
ORDER BY thread_id
"""


@dataclass(frozen=True)
class PurgeStats:
    deleted_threads: int
    cutoff: datetime


async def expired_thread_ids(pool: AsyncConnectionPool, cutoff: datetime) -> list[str]:
    """Threads whose last checkpoint is older than `cutoff`."""
    async with pool.connection() as conn, conn.cursor(row_factory=tuple_row) as cur:
        await cur.execute(EXPIRED_THREADS_SQL, (cutoff,))
        rows = await cur.fetchall()
    return [row[0] for row in rows]


async def purge_old_chats(
    database_url: str, *, retention_days: int, now: datetime | None = None
) -> PurgeStats:
    """Delete every thread idle for more than `retention_days`; safe to run again.

    `now` is for tests; the job uses the current time. A customer who sends a message in
    the moment between the select and the delete of their 90-day-old thread loses it and
    starts a new chat, which is acceptable for a nightly job.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    async with open_checkpointer(database_url) as saver:
        thread_ids = await expired_thread_ids(_pool(saver), cutoff)
        for thread_id in thread_ids:
            await saver.adelete_thread(thread_id)
    logger.info(
        "chat purge finished",
        extra={
            "event": "chat_purge",
            "deleted_threads": len(thread_ids),
            "retention_days": retention_days,
        },
    )
    return PurgeStats(deleted_threads=len(thread_ids), cutoff=cutoff)


def _pool(saver: AsyncPostgresSaver) -> AsyncConnectionPool:
    # open_checkpointer always builds the saver on a pool (its `conn` attribute).
    pool = saver.conn
    if not isinstance(pool, AsyncConnectionPool):
        raise TypeError("expected the checkpointer to run on a connection pool")
    return pool
