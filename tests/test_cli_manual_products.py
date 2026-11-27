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


# --- martom migrate (R1-1) and a broken TOML in martom embed (R1-2) ---------------------


def test_migrate_syncs_the_manual_products_and_a_second_run_prints_only_nothing_to_apply(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The one-off migrate task runs on every deploy, so a changed TOML price reaches the
    # search price filter at once, not at the next nightly embed (R1-1).
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert cli.main(["migrate", "--dir", str(MIGRATIONS)]) == 0
    assert (
        "manual products: inserted 1, updated 0, unchanged 0, deleted 0" in capsys.readouterr().out
    )
    assert cli.main(["migrate", "--dir", str(MIGRATIONS)]) == 0
    # CI's "migrate again" step needs exactly this output.
    assert capsys.readouterr().out == "nothing to apply\n"
    assert manual_rows(database_url) == [("anchor_m8x70", "Anker M8×70", False)]


def test_migrate_picks_up_a_changed_price(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    toml = tmp_path / "manual.toml"
    toml.write_text(TOML, encoding="utf-8")
    monkeypatch.setattr(cli, "MANUAL_PRODUCTS_PATH", toml)
    assert cli.main(["migrate", "--dir", str(MIGRATIONS)]) == 0
    capsys.readouterr()
    toml.write_text(TOML.replace('"0.80"', '"0.90"'), encoding="utf-8")

    assert cli.main(["migrate", "--dir", str(MIGRATIONS)]) == 0
    assert "manual products: inserted 0, updated 1" in capsys.readouterr().out


def test_migrate_without_the_manual_product_table_skips_the_sync(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # A migrations folder that doesn't create the table (as in the migrate tests).
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "001_x.sql").write_text("CREATE TABLE x (id int);", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert cli.main(["migrate", "--dir", str(folder)]) == 0
    assert "manual products" not in capsys.readouterr().out


def test_migrate_with_an_invalid_toml_exits_1(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    bad = tmp_path / "manual.toml"
    bad.write_text(TOML.replace('"0.80"', "0.80"), encoding="utf-8")
    monkeypatch.setattr(cli, "MANUAL_PRODUCTS_PATH", bad)
    assert cli.main(["migrate", "--dir", str(MIGRATIONS)]) == 1
    assert "price_km" in capsys.readouterr().err


def test_embed_with_an_invalid_toml_still_embeds_the_products_and_exits_1(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    run_migrations(database_url, MIGRATIONS)
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO olx_listing (id, slug, title, summary, detail_hash)"
            " VALUES (1, 'tiplo-1', 'TIPLO', '{}', 'h')"
        )
        conn.execute(
            "INSERT INTO product (listing_id, detail_hash, product_type, extraction, model,"
            " prompt_version, search_text) VALUES (1, 'h', 'tiplo', '{}', 'm', 'v', 'TIPLO')"
        )
    bad = tmp_path / "manual.toml"
    bad.write_text(TOML.replace('"0.80"', "0.80"), encoding="utf-8")
    monkeypatch.setattr(cli, "MANUAL_PRODUCTS_PATH", bad)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")

    assert cli.main(["embed"]) == 1
    out = capsys.readouterr().out
    assert "manual products: sync failed" in out
    assert "products: embedded 1, unchanged 0" in out
