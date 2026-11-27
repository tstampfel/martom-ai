"""`martom load-manual-products`, and `martom embed` syncing manual products first (#112)."""

# The tests quote the name with the real "×" sign, as in the TOML.
# ruff: noqa: RUF001, RUF003

from pathlib import Path

import psycopg
import pytest

from martom import cli
from martom.migrate import run_migrations

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

TOML = """
[anchor_m8x70]
name = "Anker M8×70"
price_km = "0.80"
source = "test"
last_confirmed = 2026-11-26
"""


def manual_rows(database_url: str) -> list[tuple]:
    with psycopg.connect(database_url) as conn:
        return conn.execute(
            "SELECT key, name, embedding_model IS NOT NULL FROM manual_product ORDER BY key"
        ).fetchall()


def test_loads_the_toml_and_a_second_run_changes_nothing(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_migrations(database_url, MIGRATIONS)
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert cli.main(["load-manual-products"]) == 0
    assert "inserted 1, updated 0, unchanged 0, deleted 0" in capsys.readouterr().out
    assert cli.main(["load-manual-products"]) == 0
    assert "inserted 0, updated 0, unchanged 1, deleted 0" in capsys.readouterr().out
    assert manual_rows(database_url) == [("anchor_m8x70", "Anker M8×70", False)]


def test_a_given_file_replaces_the_rows(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    run_migrations(database_url, MIGRATIONS)
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert cli.main(["load-manual-products"]) == 0
    empty = tmp_path / "manual.toml"
    empty.write_text("", encoding="utf-8")

    assert cli.main(["load-manual-products", "--file", str(empty)]) == 0
    assert "deleted 1" in capsys.readouterr().out
    assert manual_rows(database_url) == []


def test_an_invalid_file_exits_1_and_loads_nothing(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    run_migrations(database_url, MIGRATIONS)
    monkeypatch.setenv("DATABASE_URL", database_url)
    bad = tmp_path / "manual.toml"
    bad.write_text(TOML.replace('"0.80"', "0.80"), encoding="utf-8")

    assert cli.main(["load-manual-products", "--file", str(bad)]) == 1
    assert "price_km" in capsys.readouterr().err
    assert manual_rows(database_url) == []


def test_embed_syncs_the_manual_products_first_and_embeds_them(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_migrations(database_url, MIGRATIONS)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")

    assert cli.main(["embed"]) == 0
    out = capsys.readouterr().out
    assert "manual products: inserted 1, updated 0, unchanged 0, deleted 0" in out
    assert "manual products: embedded 1, unchanged 0" in out
    assert manual_rows(database_url) == [("anchor_m8x70", "Anker M8×70", True)]
