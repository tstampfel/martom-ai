"""`martom embed`: settings checks and exit codes (fake provider, real throwaway database)."""

from pathlib import Path

import psycopg
import pytest

from martom import cli
from martom.migrate import run_migrations

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_by_default_it_uses_cohere_and_exits_1_without_its_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    assert cli.main(["embed"]) == 1
    assert "COHERE_API_KEY" in capsys.readouterr().err


def test_cohere_without_its_key_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    assert cli.main(["embed"]) == 1
    assert "COHERE_API_KEY" in capsys.readouterr().err


def test_embeds_with_the_fake_provider_and_prints_the_counts(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")

    assert cli.main(["embed"]) == 0
    out = capsys.readouterr().out
    assert "fake/deterministic" in out
    assert "products: embedded 1, unchanged 0" in out
    assert cli.main(["embed"]) == 0
    assert "products: embedded 0, unchanged 1" in capsys.readouterr().out


def test_a_database_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nothing@localhost:1/none")
    assert cli.main(["embed"]) == 1
