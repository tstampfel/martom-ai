"""Unit tests for migration discovery. They need no database."""

import re
from pathlib import Path

import pytest

from martom.cli import main
from martom.migrate import MigrationError, discover_migrations


def write(directory: Path, name: str, sql: str = "SELECT 1;") -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_discovers_migrations_in_numeric_order(tmp_path: Path) -> None:
    write(tmp_path, "010_later.sql")
    write(tmp_path, "002_second.sql")
    write(tmp_path, "001_first.sql")

    names = [m.name for m in discover_migrations(tmp_path)]

    assert names == ["001_first.sql", "002_second.sql", "010_later.sql"]


def test_ignores_files_that_are_not_sql(tmp_path: Path) -> None:
    write(tmp_path, "001_first.sql")
    write(tmp_path, "README.md", "notes")

    assert [m.name for m in discover_migrations(tmp_path)] == ["001_first.sql"]


@pytest.mark.parametrize("bad_name", ["1_short.sql", "001-dash.sql", "001_.sql", "abc_x.sql"])
def test_rejects_sql_files_with_a_bad_name(tmp_path: Path, bad_name: str) -> None:
    write(tmp_path, bad_name)

    with pytest.raises(MigrationError, match=re.escape(bad_name)):
        discover_migrations(tmp_path)


def test_rejects_two_migrations_with_the_same_number(tmp_path: Path) -> None:
    write(tmp_path, "003_one.sql")
    write(tmp_path, "003_other.sql")

    with pytest.raises(MigrationError, match="003"):
        discover_migrations(tmp_path)


def test_checksum_changes_when_the_file_changes(tmp_path: Path) -> None:
    write(tmp_path, "001_first.sql", "SELECT 1;")
    before = discover_migrations(tmp_path)[0].checksum

    write(tmp_path, "001_first.sql", "SELECT 2;")
    after = discover_migrations(tmp_path)[0].checksum

    assert before != after


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="not found"):
        discover_migrations(tmp_path / "nope")


def test_repo_migrations_are_valid() -> None:
    """The real migrations/ folder must always pass discovery."""
    repo_migrations = Path(__file__).resolve().parents[1] / "migrations"

    names = [m.name for m in discover_migrations(repo_migrations)]

    assert names[0] == "001_extensions.sql"


def test_cli_migrate_without_database_url_fails_clearly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert main(["migrate"]) == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err
