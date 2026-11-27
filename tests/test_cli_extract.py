"""`martom extract`: settings checks and exit codes (fake model, real throwaway database)."""

from collections.abc import Sequence
from pathlib import Path

import psycopg
import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from martom import cli
from martom.extract.extractor import Extractor
from martom.extract.schema import ExtractedProduct
from martom.migrate import run_migrations
from martom.settings import Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

PRODUCT = ExtractedProduct(
    product_type="tiplo", brand=None, material=None, wire_thickness=None, mesh=None,
    colours=[], uses=[], compatible_with=[], synonyms=[], variants=[],
)  # fmt: skip


def test_without_an_api_key_it_exits_1_before_touching_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert cli.main(["extract"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_limit_must_be_positive() -> None:
    with pytest.raises(SystemExit):
        cli.main(["extract", "--limit", "0"])


@pytest.mark.parametrize(
    ("failing", "exit_code"),
    [
        (set(), 0),  # succeeded
        ({2}, 2),  # partial: one of two failed
        ({1, 2}, 1),  # failed: nothing was extracted
    ],
)
def test_exit_code_follows_the_run_status(
    database_url: str, monkeypatch: pytest.MonkeyPatch, failing: set[int], exit_code: int
) -> None:
    run_migrations(database_url, MIGRATIONS)
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO olx_listing (id, slug, title, summary, description_text, detail_hash)"
            " VALUES (1, 'tiplo-1', 'TIPLO', '{}', 'TIPLO SX6 - 0,15KM', 'h'),"
            " (2, 'tiplo-2', 'TIPLO', '{}', 'TIPLO SX8 - 0,20KM', 'h')"
        )

    def fake(messages: Sequence[BaseMessage]) -> ExtractedProduct:
        if any(f'<listing id="{i}">' in messages[1].text for i in failing):
            raise RuntimeError("model unavailable")
        return PRODUCT

    def make_fake_extractor(settings: Settings) -> Extractor:
        return Extractor(RunnableLambda(fake), model_name="fake", retry_wait=False)

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(cli, "make_extractor", make_fake_extractor)

    assert cli.main(["extract"]) == exit_code
