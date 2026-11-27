"""Loading knowledge chunks into `knowledge_chunk` against a real Postgres (see conftest)."""

from datetime import datetime
from pathlib import Path

import psycopg
import pytest

from martom.cli import main
from martom.knowledge import KnowledgeError, LoadStats, run_load_knowledge
from martom.migrate import run_migrations

REPO = Path(__file__).resolve().parents[1]

CHUNK = """\
---
id: {id}
title: Naslov {id}
topic: posts
sources:
  - "Toni, 26.11.2026"
status: {status}
---

# Naslov

Tekst za {id}.
"""


@pytest.fixture
def migrated(database_url: str) -> str:
    run_migrations(database_url, REPO / "migrations")
    return database_url


def write_chunk(directory: Path, chunk_id: str, status: str = "draft") -> Path:
    path = directory / f"{chunk_id}.md"
    path.write_text(CHUNK.format(id=chunk_id, status=status), encoding="utf-8")
    return path


def rows(database_url: str) -> list[tuple]:
    with psycopg.connect(database_url) as conn:
        return conn.execute(
            "SELECT id, title, topic, sources, status, body, source_path"
            " FROM knowledge_chunk ORDER BY id"
        ).fetchall()


def updated_at(database_url: str, chunk_id: str) -> datetime:
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "SELECT updated_at FROM knowledge_chunk WHERE id = %s", (chunk_id,)
        ).fetchone()
    assert row is not None
    return row[0]


def test_loads_every_chunk_with_its_front_matter(migrated: str, tmp_path: Path) -> None:
    write_chunk(tmp_path, "a")
    write_chunk(tmp_path, "b", status="approved")

    stats = run_load_knowledge(migrated, tmp_path)

    assert stats == LoadStats(inserted=2, updated=0, unchanged=0, deleted=0)
    assert rows(migrated) == [
        (
            "a",
            "Naslov a",
            "posts",
            ["Toni, 26.11.2026"],
            "draft",
            "# Naslov\n\nTekst za a.",
            "a.md",
        ),
        (
            "b",
            "Naslov b",
            "posts",
            ["Toni, 26.11.2026"],
            "approved",
            "# Naslov\n\nTekst za b.",
            "b.md",
        ),
    ]


def test_second_load_of_the_same_files_changes_nothing(migrated: str, tmp_path: Path) -> None:
    write_chunk(tmp_path, "a")
    run_load_knowledge(migrated, tmp_path)
    before = updated_at(migrated, "a")

    stats = run_load_knowledge(migrated, tmp_path)

    assert stats == LoadStats(inserted=0, updated=0, unchanged=1, deleted=0)
    assert updated_at(migrated, "a") == before


def test_edited_chunk_is_updated_in_place(migrated: str, tmp_path: Path) -> None:
    write_chunk(tmp_path, "a")
    run_load_knowledge(migrated, tmp_path)
    before = updated_at(migrated, "a")

    write_chunk(tmp_path, "a", status="approved")  # Toni approved it
    stats = run_load_knowledge(migrated, tmp_path)

    assert stats == LoadStats(inserted=0, updated=1, unchanged=0, deleted=0)
    assert [row[4] for row in rows(migrated)] == ["approved"]
    assert updated_at(migrated, "a") > before


def test_chunk_whose_file_is_gone_is_deleted(migrated: str, tmp_path: Path) -> None:
    write_chunk(tmp_path, "a")
    removed = write_chunk(tmp_path, "b")
    run_load_knowledge(migrated, tmp_path)

    removed.unlink()
    stats = run_load_knowledge(migrated, tmp_path)

    assert stats == LoadStats(inserted=0, updated=0, unchanged=1, deleted=1)
    assert [row[0] for row in rows(migrated)] == ["a"]


def test_invalid_chunk_loads_nothing(migrated: str, tmp_path: Path) -> None:
    write_chunk(tmp_path, "a")
    (tmp_path / "b.md").write_text("# no front matter\n", encoding="utf-8")

    with pytest.raises(KnowledgeError):
        run_load_knowledge(migrated, tmp_path)

    assert rows(migrated) == []


def test_status_outside_draft_and_approved_is_refused_by_the_table(migrated: str) -> None:
    with psycopg.connect(migrated) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO knowledge_chunk"
            " (id, title, topic, sources, status, body, source_path, content_hash)"
            " VALUES ('x', 't', 'p', '{}', 'done', 'b', 'x.md', 'h')"
        )


def test_repo_knowledge_folder_loads(migrated: str) -> None:
    stats = run_load_knowledge(migrated, REPO / "knowledge")

    assert stats.inserted >= 10
    assert len(rows(migrated)) == stats.inserted


def test_cli_loads_the_folder(
    migrated: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_chunk(tmp_path, "a")
    monkeypatch.setenv("DATABASE_URL", migrated)

    assert main(["load-knowledge", "--dir", str(tmp_path)]) == 0

    assert "inserted 1, updated 0, unchanged 0, deleted 0" in capsys.readouterr().out
    assert [row[0] for row in rows(migrated)] == ["a"]


def test_cli_reports_an_invalid_folder(
    migrated: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated)

    assert main(["load-knowledge", "--dir", str(tmp_path / "missing")]) == 1

    assert "not found" in capsys.readouterr().err
