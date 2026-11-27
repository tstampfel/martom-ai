"""Postgres checkpointer tests against a real Postgres (see conftest.database_url)."""

from pathlib import Path

import psycopg
import pytest
from langgraph.checkpoint.base import empty_checkpoint

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
    setup_checkpointer(database_url)
    setup_checkpointer(database_url)

    assert existing_tables(database_url, CHECKPOINT_TABLES) == set(CHECKPOINT_TABLES)


def test_cli_migrate_sets_up_the_checkpointer_on_an_empty_db_and_twice(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert main(["migrate", "--dir", str(REPO_MIGRATIONS)]) == 0
    assert main(["migrate", "--dir", str(REPO_MIGRATIONS)]) == 0

    assert existing_tables(database_url, CHECKPOINT_TABLES) == set(CHECKPOINT_TABLES)
    # Our SQL migrations ran first, in the same command.
    assert existing_tables(database_url, ("olx_listing",)) == {"olx_listing"}
    out = capsys.readouterr().out
    assert "nothing to apply" in out
    assert out.count("checkpointer tables ready") == 2


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
