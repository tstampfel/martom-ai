"""`martom report` prints Toni's Bosnian report from the database."""

from pathlib import Path

import psycopg
import pytest

from martom import cli
from martom.migrate import run_migrations

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_report_prints_open_issues_grouped_by_priority(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_migrations(database_url, MIGRATIONS)
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO olx_listing (id, slug, title, summary) VALUES"
            " (1, 'panel-153-1', 'PANEL 153', '{}'), (2, 'zica-2', 'ŽICA', '{}')"
        )
        conn.execute(
            "INSERT INTO review_issue (fingerprint, kind, listing_id, priority, message, status)"
            " VALUES ('a', 'used_state', 2, 'medium', 'Oglas 2: korišteno.', 'open'),"
            " ('b', 'implausible_dimension', 1, 'high', 'Oglas 1: 2500CM.', 'open'),"
            " ('c', 'typo', 1, 'low', 'Oglas 1: tipfeler.', 'ignored')"
        )
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert cli.main(["report"]) == 0

    out = capsys.readouterr().out
    assert out.index("VISOK PRIORITET (1)") < out.index("SREDNJI PRIORITET (1)")
    assert "https://martom.olx.ba/artikal/panel-153-1" in out
    assert "https://martom.olx.ba/artikal/zica-2" in out
    assert "tipfeler" not in out
    assert "NIZAK PRIORITET" not in out


def test_report_exits_1_when_the_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none?connect_timeout=1")

    assert cli.main(["report"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "report failed" in captured.err
