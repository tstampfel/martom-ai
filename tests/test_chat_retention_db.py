"""`martom purge-chats` against a real Postgres (see conftest.database_url).

Checkpoints are written with the library's own saver and a chosen `ts`, so the job sees
the same rows the API writes, only with fake timestamps.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from martom.checkpointer import open_checkpointer, setup_checkpointer
from martom.cli import main
from martom.retention import purge_old_chats

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 28, 3, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def write_checkpoint(saver: AsyncPostgresSaver, thread: str, ts: datetime) -> None:
    """One checkpoint (with a pending write) on `thread`, stamped `ts`."""
    checkpoint = empty_checkpoint()
    checkpoint["ts"] = ts.isoformat()
    config: dict[str, Any] = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    saved = await saver.aput(config, checkpoint, {"source": "input", "step": 0}, {})
    await saver.aput_writes(saved, [("messages", "Zovem se Ivan")], task_id="task-1")


def threads_in(database_url: str, table: str) -> set[str]:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(f"SELECT DISTINCT thread_id FROM {table}").fetchall()  # noqa: S608
    return {row[0] for row in rows}


async def seed(database_url: str) -> None:
    setup_checkpointer(database_url)
    async with open_checkpointer(database_url) as saver:
        await write_checkpoint(saver, "old", NOW - timedelta(days=120))
        await write_checkpoint(saver, "just-over", NOW - timedelta(days=90, minutes=1))
        await write_checkpoint(saver, "just-under", NOW - timedelta(days=89, hours=23))
        await write_checkpoint(saver, "fresh", NOW - timedelta(days=1))
        # A thread is kept while its *last* checkpoint is recent, however old it started.
        await write_checkpoint(saver, "returning", NOW - timedelta(days=200))
        await write_checkpoint(saver, "returning", NOW - timedelta(days=3))


async def test_deletes_only_threads_idle_longer_than_the_retention(database_url: str) -> None:
    await seed(database_url)

    stats = await purge_old_chats(database_url, retention_days=90, now=NOW)

    assert stats.deleted_threads == 2
    assert stats.cutoff == NOW - timedelta(days=90)
    kept = {"just-under", "fresh", "returning"}
    assert threads_in(database_url, "checkpoints") == kept
    assert threads_in(database_url, "checkpoint_writes") == kept


async def test_a_second_run_deletes_nothing(database_url: str) -> None:
    await seed(database_url)
    await purge_old_chats(database_url, retention_days=90, now=NOW)

    again = await purge_old_chats(database_url, retention_days=90, now=NOW)

    assert again.deleted_threads == 0
    assert threads_in(database_url, "checkpoints") == {"just-under", "fresh", "returning"}


async def test_never_touches_order_request(database_url: str) -> None:
    # order_request arrives with #27; a stand-in with old rows proves the job leaves any
    # other table alone, however old its rows are.
    await seed(database_url)
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "CREATE TABLE order_request (id serial PRIMARY KEY, thread_id text, "
            "created_at timestamptz NOT NULL)"
        )
        conn.execute(
            "INSERT INTO order_request (thread_id, created_at) VALUES (%s, %s), (%s, %s)",
            ("old", NOW - timedelta(days=120), "fresh", NOW - timedelta(days=1)),
        )

    await purge_old_chats(database_url, retention_days=90, now=NOW)

    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT thread_id FROM order_request ORDER BY id").fetchall()
    assert [row[0] for row in rows] == ["old", "fresh"]


def test_cli_prints_the_count_and_never_the_chat(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The CLI uses the real clock, so these timestamps are relative to it.
    today = datetime.now(UTC)
    setup_checkpointer(database_url)

    async def seed_today() -> None:
        async with open_checkpointer(database_url) as saver:
            await write_checkpoint(saver, "stale", today - timedelta(days=40))
            await write_checkpoint(saver, "recent", today - timedelta(days=5))

    # A plain (sync) test: main() runs its own event loop, like the real command.
    asyncio.run(seed_today())
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CHAT_RETENTION_DAYS", "30")

    exit_code = main(["purge-chats"])

    out = capsys.readouterr()
    assert exit_code == 0
    assert out.out.strip() == "purge-chats: deleted 1 threads idle for more than 30 days"
    assert "Ivan" not in out.out + out.err
    assert threads_in(database_url, "checkpoints") == {"recent"}


def test_cli_fails_when_the_tables_are_missing(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)

    exit_code = main(["purge-chats"])

    assert exit_code == 1
    assert "martom migrate" in capsys.readouterr().err
