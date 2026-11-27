"""Unit tests for migration discovery. They need no database."""

import re
from pathlib import Path

import psycopg
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


def test_cli_migrate_without_database_url_uses_the_local_compose_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    used_urls: list[str] = []

    def fake_run_migrations(database_url: str, directory: Path) -> list[str]:
        used_urls.append(database_url)
        return []

    monkeypatch.setattr("martom.cli.run_migrations", fake_run_migrations)
    monkeypatch.setattr("martom.cli.setup_checkpointer", used_urls.append)

    assert main(["migrate"]) == 0
    # Same fake value as DATABASE_URL in .env.example, for the SQL and the checkpointer.
    assert used_urls == ["postgresql://martom:martom@localhost:5432/martom"] * 2


def test_cli_migrate_prefers_database_url_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:6543/other")
    used_urls: list[str] = []

    def fake_run_migrations(database_url: str, directory: Path) -> list[str]:
        used_urls.append(database_url)
        return []

    monkeypatch.setattr("martom.cli.run_migrations", fake_run_migrations)
    monkeypatch.setattr("martom.cli.setup_checkpointer", used_urls.append)

    assert main(["migrate"]) == 0
    assert used_urls == ["postgresql://u:p@db.example:6543/other"] * 2


def test_cli_migrate_sets_up_the_checkpointer_after_the_sql_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run_migrations(database_url: str, directory: Path) -> list[str]:
        calls.append("sql")
        return []

    def fake_setup_checkpointer(database_url: str) -> None:
        calls.append("checkpointer")

    monkeypatch.setattr("martom.cli.run_migrations", fake_run_migrations)
    monkeypatch.setattr("martom.cli.setup_checkpointer", fake_setup_checkpointer)

    assert main(["migrate"]) == 0
    assert calls == ["sql", "checkpointer"]


def test_cli_migrate_skips_the_checkpointer_when_a_migration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_run_migrations(database_url: str, directory: Path) -> list[str]:
        raise MigrationError("002_bad.sql failed")

    setup_urls: list[str] = []
    monkeypatch.setattr("martom.cli.run_migrations", failing_run_migrations)
    monkeypatch.setattr("martom.cli.setup_checkpointer", setup_urls.append)

    assert main(["migrate"]) == 1
    assert setup_urls == []


def test_cli_migrate_fails_when_checkpointer_setup_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_setup(database_url: str) -> None:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr("martom.cli.run_migrations", lambda url, directory: [])
    monkeypatch.setattr("martom.cli.setup_checkpointer", failing_setup)

    assert main(["migrate"]) == 1
    assert "checkpointer setup failed: connection refused" in capsys.readouterr().err


def test_cli_migrate_with_unreachable_database_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, "001_first.sql")
    # Port 1 refuses connections immediately; no database server is needed.
    monkeypatch.setenv("DATABASE_URL", "postgresql://martom:martom@127.0.0.1:1/martom")

    assert main(["migrate", "--dir", str(tmp_path)]) == 1
    assert "migration failed" in capsys.readouterr().err


def test_missing_directory_error_explains_how_to_fix(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="--dir"):
        discover_migrations(tmp_path / "nope")
