"""Postgres checkpointer tests against a real Postgres (see conftest.database_url)."""

import threading
from pathlib import Path

import psycopg
import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres import PostgresSaver

from martom.checkpointer import open_checkpointer, setup_checkpointer
from martom.cli import main

pytestmark = pytest.mark.anyio

REPO_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
CHECKPOINT_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def existing_tables(database_url: str, names: tuple[str, ...]) -> set[str]:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
            (list(names),),
        ).fetchall()
    return {row[0] for row in rows}


def test_setup_creates_the_checkpoint_tables_and_reruns_safely(database_url: str) -> None:
    assert setup_checkpointer(database_url) == len(PostgresSaver.MIGRATIONS)
    assert setup_checkpointer(database_url) == 0

    assert existing_tables(database_url, CHECKPOINT_TABLES) == set(CHECKPOINT_TABLES)


def test_concurrent_setups_wait_for_each_other(database_url: str) -> None:
    # On an empty database the library runs CREATE INDEX CONCURRENTLY while holding the
    # advisory lock. A second runner blocked in pg_advisory_lock would deadlock with it
    # (review m4-3b R1-2); it must wait instead.
    errors: list[BaseException] = []

    def run() -> None:
        try:
            setup_checkpointer(database_url)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert existing_tables(database_url, CHECKPOINT_TABLES) == set(CHECKPOINT_TABLES)


def test_cli_migrate_sets_up_the_checkpointer_on_an_empty_db_and_twice(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert main(["migrate", "--dir", str(REPO_MIGRATIONS)]) == 0
    first = capsys.readouterr().out
    assert main(["migrate", "--dir", str(REPO_MIGRATIONS)]) == 0
    second = capsys.readouterr().out

    assert existing_tables(database_url, CHECKPOINT_TABLES) == set(CHECKPOINT_TABLES)
    # Our SQL migrations ran first, in the same command.
    assert existing_tables(database_url, ("olx_listing",)) == {"olx_listing"}
    assert f"checkpointer: applied {len(PostgresSaver.MIGRATIONS)} library migrations" in first
    # CI's "Migrate again (must be a no-op)" step requires exactly this output (R1-1).
    assert second == "nothing to apply\n"


async def test_checkpoint_survives_a_new_checkpointer(database_url: str) -> None:
    # Two separate open_checkpointer() blocks stand in for two processes: the second
    # one only finds the checkpoint because it is in Postgres.
    setup_checkpointer(database_url)
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": ["Trebam ogradu"]}
    checkpoint["channel_versions"] = {"messages": "1"}

    async with open_checkpointer(database_url) as saver:
        await saver.aput(config, checkpoint, {"source": "input", "step": 0}, {"messages": "1"})

    async with open_checkpointer(database_url) as saver:
        loaded = await saver.aget_tuple(config)
        other = await saver.aget_tuple({"configurable": {"thread_id": "thread-2"}})

    assert loaded is not None
    assert loaded.checkpoint["id"] == checkpoint["id"]
    assert loaded.checkpoint["channel_values"] == {"messages": ["Trebam ogradu"]}
    assert other is None
