"""Chat retention: delete customer chats that have been idle longer than the retention.

Decided by Tomislav (#129, 27 Nov 2026): chats are kept 90 days (`CHAT_RETENTION_DAYS`),
then the nightly `martom purge-chats` task deletes them. A chat is one checkpointer
thread; its age is the time of its *last* checkpoint, so a customer who comes back keeps
the whole thread for another 90 days.

The job only ever deletes through the saver's `adelete_thread`, which touches the
library's `checkpoints`, `checkpoint_blobs` and `checkpoint_writes` rows of that thread
and nothing else: orders (`order_request`, kept 30 days by their own job, #27) are never affected.

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
# is its newest checkpoint across all namespaces (subgraphs write their own). A thread
# with no `ts` at all (max() is NULL) can't be dated: it is deleted too, never kept
# forever, and counted as `undated`.
EXPIRED_THREADS_SQL = """
SELECT thread_id, max((checkpoint ->> 'ts')::timestamptz) IS NULL AS undated
FROM checkpoints
GROUP BY thread_id
HAVING max((checkpoint ->> 'ts')::timestamptz) IS NULL
    OR max((checkpoint ->> 'ts')::timestamptz) < %s
ORDER BY thread_id
"""


@dataclass(frozen=True)
class PurgeStats:
    deleted_threads: int
    cutoff: datetime
    # Of the deleted threads, those without any checkpoint timestamp (not expected: the
    # library always writes one; a WARNING is logged when there are any).
    undated_threads: int = 0


async def expired_threads(pool: AsyncConnectionPool, cutoff: datetime) -> list[tuple[str, bool]]:
    """(thread id, undated) for each thread whose last checkpoint is older than `cutoff`,
    or which has no checkpoint timestamp at all."""
    async with pool.connection() as conn, conn.cursor(row_factory=tuple_row) as cur:
        await cur.execute(EXPIRED_THREADS_SQL, (cutoff,))
        rows = await cur.fetchall()
    return [(row[0], row[1]) for row in rows]


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
        threads = await expired_threads(_pool(saver), cutoff)
        for thread_id, _undated in threads:
            await saver.adelete_thread(thread_id)
    undated = sum(1 for _thread_id, is_undated in threads if is_undated)
    if undated:
        logger.warning(
            "deleted chats without a checkpoint timestamp",
            extra={"event": "chat_purge_undated", "undated_threads": undated},
        )
    logger.info(
        "chat purge finished",
        extra={
            "event": "chat_purge",
            "deleted_threads": len(threads),
            "retention_days": retention_days,
        },
    )
    return PurgeStats(deleted_threads=len(threads), cutoff=cutoff, undated_threads=undated)


def _pool(saver: AsyncPostgresSaver) -> AsyncConnectionPool:
    # open_checkpointer always builds the saver on a pool (its `conn` attribute).
    pool = saver.conn
    if not isinstance(pool, AsyncConnectionPool):
        raise TypeError("expected the checkpointer to run on a connection pool")
    return pool
