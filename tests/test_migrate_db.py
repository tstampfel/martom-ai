"""Migration runner tests against a real Postgres (see conftest.database_url)."""

import re
from pathlib import Path

import psycopg
import pytest

from martom.cli import main
from martom.migrate import MigrationError, run_migrations

REPO_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def recorded(database_url: str) -> list[str]:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT filename FROM schema_migrations ORDER BY filename").fetchall()
    return [row[0] for row in rows]


def table_exists(database_url: str, table: str) -> bool:
    with psycopg.connect(database_url) as conn:
        row = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()
    assert row is not None
    return bool(row[0])


def test_applies_pending_migrations_in_order_and_records_them(
    database_url: str, tmp_path: Path
) -> None:
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    write(tmp_path, "002_b.sql", "CREATE TABLE b (a_id int);")

    applied = run_migrations(database_url, tmp_path)

    assert applied == ["001_a.sql", "002_b.sql"]
    assert recorded(database_url) == ["001_a.sql", "002_b.sql"]
    assert table_exists(database_url, "b")


def test_second_run_skips_applied_migrations(database_url: str, tmp_path: Path) -> None:
    # Not idempotent on purpose: running it twice would fail.
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    run_migrations(database_url, tmp_path)

    assert run_migrations(database_url, tmp_path) == []


def test_only_new_migrations_run_later(database_url: str, tmp_path: Path) -> None:
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    run_migrations(database_url, tmp_path)
    write(tmp_path, "002_b.sql", "CREATE TABLE b (id int);")

    assert run_migrations(database_url, tmp_path) == ["002_b.sql"]


def test_failing_migration_is_rolled_back_and_not_recorded(
    database_url: str, tmp_path: Path
) -> None:
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    write(tmp_path, "002_bad.sql", "CREATE TABLE half (id int); SELECT * FROM missing_table;")

    with pytest.raises(MigrationError, match=re.escape("002_bad.sql")):
        run_migrations(database_url, tmp_path)

    assert recorded(database_url) == ["001_a.sql"]
    assert not table_exists(database_url, "half")


def test_migration_and_its_record_commit_together(database_url: str, tmp_path: Path) -> None:
    # The SQL succeeds but pre-fills its own row, so the runner's bookkeeping INSERT fails.
    # The table it created must be rolled back with it.
    write(
        tmp_path,
        "001_a.sql",
        "CREATE TABLE a (id int);"
        " INSERT INTO schema_migrations (filename, checksum) VALUES ('001_a.sql', 'x');",
    )

    with pytest.raises(MigrationError, match=re.escape("001_a.sql")):
        run_migrations(database_url, tmp_path)

    assert recorded(database_url) == []
    assert not table_exists(database_url, "a")


def test_edited_applied_migration_is_an_error(database_url: str, tmp_path: Path) -> None:
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    run_migrations(database_url, tmp_path)
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id bigint);")

    with pytest.raises(MigrationError, match=re.escape("001_a.sql")):
        run_migrations(database_url, tmp_path)


def test_repo_migrations_create_extensions_and_text_search_config(database_url: str) -> None:
    run_migrations(database_url, REPO_MIGRATIONS)

    with psycopg.connect(database_url) as conn:
        extensions = {row[0] for row in conn.execute("SELECT extname FROM pg_extension")}
        # A phone query: no diacritics, other word forms than the listing text.
        row = conn.execute(
            "SELECT to_tsvector('martom', 'Pocinčana ŽICA za ogradu')"
            " @@ plainto_tsquery('martom', 'pocincane zice ograda')"
        ).fetchone()

    assert {"vector", "unaccent", "pg_trgm"} <= extensions
    assert row == (True,)


@pytest.mark.parametrize(
    ("listing_text", "query"),
    [
        ("Mreža-50x50 pletena", "mreza-50x50"),  # numhword / hword_numpart tokens
        ("Žica2,5 pocinčana", "zica2,5"),  # numword token
    ],
)
def test_martom_config_unaccents_words_with_digits(
    database_url: str, listing_text: str, query: str
) -> None:
    run_migrations(database_url, REPO_MIGRATIONS)

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "SELECT to_tsvector('martom', %s) @@ plainto_tsquery('martom', %s)",
            (listing_text, query),
        ).fetchone()

    assert row == (True,)


def test_repo_migrations_sql_rerun_safely(database_url: str) -> None:
    """Every repo migration can run twice, e.g. if schema_migrations was lost."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        for path in sorted(REPO_MIGRATIONS.glob("*.sql")):
            sql_text = path.read_text(encoding="utf-8")
            conn.execute(sql_text)  # type: ignore[arg-type]
            conn.execute(sql_text)  # type: ignore[arg-type]


def test_cli_migrate_uses_database_url(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert main(["migrate", "--dir", str(tmp_path)]) == 0
    assert main(["migrate", "--dir", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "applied 001_a.sql" in out
    assert "nothing to apply" in out
