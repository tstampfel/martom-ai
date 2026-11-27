"""GET /admin/review-issues: token auth and the JSON report.

Most tests swap the database for a small fake source; the last one reads a real Postgres.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from martom.api.app import create_app
from martom.migrate import run_migrations
from martom.review.report import ReportIssue, ReportListing
from martom.settings import Settings

TOKEN = "test-admin-token-0123456789abcdef"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

ISSUE = ReportIssue(
    id=1,
    kind="used_state",
    priority="medium",
    message='Oglas 5: stanje je „Korišteno“ </script><b>"x"</b>',
    listings=(ReportListing(5, "PANEL", "https://martom.olx.ba/artikal/panel-5"),),
    first_seen_at=datetime(2026, 11, 26, tzinfo=UTC),
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["ADMIN_TOKEN", "DATABASE_URL", "CORS_ALLOWED_ORIGINS"]:
        monkeypatch.delenv(name, raising=False)


def client(admin_token: str | None = TOKEN) -> TestClient:
    async def source() -> list[ReportIssue]:
        return [ISSUE]

    settings = Settings(admin_token=admin_token)  # type: ignore[arg-type]
    return TestClient(create_app(settings, health_checks={}, review_issue_source=source))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_admin_token_is_unset_by_default() -> None:
    assert Settings().admin_token is None


def test_admin_token_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)

    token = Settings().admin_token

    assert token is not None and token.get_secret_value() == TOKEN
    assert TOKEN not in repr(Settings())


def test_rejects_a_short_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "short")

    with pytest.raises(ValidationError):
        Settings()


def test_endpoint_is_404_when_no_token_is_configured() -> None:
    response = client(admin_token=None).get("/admin/review-issues", headers=bearer(TOKEN))

    assert response.status_code == 404


def test_missing_token_is_401() -> None:
    response = client().get("/admin/review-issues")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "issues" not in response.json()


@pytest.mark.parametrize(
    "headers",
    [
        bearer("wrong-token-0123456789abcdef-wrong"),
        bearer(TOKEN[:-1]),
        bearer(TOKEN + "x"),
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
    ],
)
def test_wrong_token_is_401(headers: dict[str, str]) -> None:
    response = client().get("/admin/review-issues", headers=headers)

    assert response.status_code == 401


def test_token_in_the_query_string_is_not_accepted() -> None:
    response = client().get(f"/admin/review-issues?token={TOKEN}")

    assert response.status_code == 401


def test_right_token_returns_the_report_grouped_by_priority() -> None:
    response = client().get("/admin/review-issues", headers=bearer(TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["counts"] == {"high": 0, "medium": 1, "low": 0}
    [issue] = body["issues"]["medium"]
    assert issue["message"] == ISSUE.message
    assert issue["listings"][0]["url"] == "https://martom.olx.ba/artikal/panel-5"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Type"] == "application/json"


def test_listing_text_is_escaped_json_not_html() -> None:
    response = client().get("/admin/review-issues", headers=bearer(TOKEN))

    # Valid JSON whose string carries the text as data; a browser won't sniff it as HTML.
    assert json.loads(response.text)["issues"]["medium"][0]["message"] == ISSUE.message
    assert response.headers["Content-Type"].startswith("application/json")


def test_reads_open_issues_from_the_database(database_url: str) -> None:
    run_migrations(database_url, MIGRATIONS)
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO olx_listing (id, slug, title, summary) VALUES (7, 'zica-7', 'ŽICA', '{}')"
        )
        conn.execute(
            "INSERT INTO review_issue (fingerprint, kind, listing_id, priority, message)"
            " VALUES ('f', 'used_state', 7, 'high', 'Oglas 7: korišteno.')"
        )
    settings = Settings(admin_token=TOKEN, database_url=database_url)  # type: ignore[arg-type]

    response = TestClient(create_app(settings, health_checks={})).get(
        "/admin/review-issues", headers=bearer(TOKEN)
    )

    assert response.status_code == 200
    [issue] = response.json()["issues"]["high"]
    assert issue["listings"] == [
        {"id": 7, "title": "ŽICA", "url": "https://martom.olx.ba/artikal/zica-7"}
    ]
