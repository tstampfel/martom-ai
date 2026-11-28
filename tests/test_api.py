"""The FastAPI app: GET /health, the request-id middleware and CORS.

Most tests swap the real Postgres/Redis checks for small fake async functions, so they
run without any server. The last test uses the real checks against the test servers.
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import martom.search.synonyms as synonyms_module
from martom.api.app import create_app
from martom.log import JsonFormatter
from martom.settings import Settings

PAGES_DEV = "https://martom-savjetnik.pages.dev"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings() reads the real environment; don't let a developer's exports change results.
    for name in ["APP_ENV", "LOG_LEVEL", "CORS_ALLOWED_ORIGINS", "DATABASE_URL", "REDIS_URL"]:
        monkeypatch.delenv(name, raising=False)


async def healthy() -> None:
    return None


async def broken() -> None:
    raise ConnectionError("connection refused by 10.0.0.5")


async def hanging() -> None:
    await asyncio.sleep(10)


def client_with(**checks: object) -> TestClient:
    return TestClient(create_app(Settings(), health_checks=checks, check_timeout=0.2))


def test_health_is_200_when_postgres_and_redis_are_up() -> None:
    response = client_with(postgres=healthy, redis=healthy).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


def test_health_is_503_when_postgres_fails() -> None:
    response = client_with(postgres=broken, redis=healthy).get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "checks": {"postgres": "error", "redis": "ok"}}


def test_health_stays_200_when_only_redis_fails() -> None:
    # M3-5: Redis is only the search cache, and search runs uncached without it, so a dead
    # Redis must not take the task out of the load balancer (an outage). It still shows.
    response = client_with(postgres=healthy, redis=broken).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "checks": {"postgres": "ok", "redis": "error"},
    }


def test_health_is_503_when_both_fail() -> None:
    response = client_with(postgres=broken, redis=broken).get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_health_does_not_leak_error_details() -> None:
    response = client_with(postgres=broken, redis=healthy).get("/health")

    assert "10.0.0.5" not in response.text


def test_health_logs_the_failure_reason(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="martom.api.health"):
        client_with(postgres=broken, redis=healthy).get("/health")

    assert any("postgres" in r.getMessage() and r.exc_info for r in caplog.records)


def test_health_treats_a_hanging_check_as_failed() -> None:
    response = client_with(postgres=hanging, redis=healthy).get("/health")

    assert response.status_code == 503
    assert response.json()["checks"] == {"postgres": "error", "redis": "ok"}


def test_every_response_gets_a_request_id_header() -> None:
    client = client_with(postgres=healthy, redis=healthy)

    first = client.get("/health").headers["X-Request-ID"]
    second = client.get("/health").headers["X-Request-ID"]

    assert len(first) == 32
    assert first != second


def test_a_well_formed_incoming_request_id_is_kept() -> None:
    response = client_with(postgres=healthy, redis=healthy).get(
        "/health", headers={"X-Request-ID": "cf-ray-1234abcd"}
    )

    assert response.headers["X-Request-ID"] == "cf-ray-1234abcd"


@pytest.mark.parametrize("bad", ["", "a" * 200, "has spaces", 'quote"injection'])
def test_a_malformed_incoming_request_id_is_replaced(bad: str) -> None:
    response = client_with(postgres=healthy, redis=healthy).get(
        "/health", headers={"X-Request-ID": bad}
    )

    assert response.headers["X-Request-ID"] != bad
    assert len(response.headers["X-Request-ID"]) == 32


def test_each_request_is_logged_with_its_request_id(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="martom.api"):
        response = client_with(postgres=healthy, redis=healthy).get("/health")

    request_id = response.headers["X-Request-ID"]
    entries = [json.loads(JsonFormatter().format(r)) for r in caplog.records]
    finished = [e for e in entries if e["message"] == "request finished"]
    assert len(finished) == 1
    assert finished[0]["request_id"] == request_id
    assert finished[0]["method"] == "GET"
    assert finished[0]["path"] == "/health"
    assert finished[0]["status_code"] == 200
    assert isinstance(finished[0]["duration_ms"], float)


def test_cors_allows_the_pages_dev_origin() -> None:
    response = client_with(postgres=healthy, redis=healthy).options(
        "/health",
        headers={"Origin": PAGES_DEV, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == PAGES_DEV


def test_cors_rejects_other_origins() -> None:
    response = client_with(postgres=healthy, redis=healthy).get(
        "/health", headers={"Origin": "https://evil.example.com"}
    )

    assert "access-control-allow-origin" not in response.headers


def test_health_with_the_real_checks(database_url: str, redis_url: str) -> None:
    settings = Settings(database_url=database_url, redis_url=redis_url)

    response = TestClient(create_app(settings)).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


def test_an_unhandled_error_is_a_500_with_a_request_id(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(Settings(), health_checks={"postgres": healthy, "redis": healthy})

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail from 10.0.0.5")

    with caplog.at_level(logging.INFO, logger="martom.api"):
        response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert "10.0.0.5" not in response.text
    request_id = response.headers["X-Request-ID"]
    # The traceback is logged while the request id is still known.
    failed = [r for r in caplog.records if r.getMessage() == "unhandled error"]
    assert len(failed) == 1
    assert failed[0].exc_info
    assert json.loads(JsonFormatter().format(failed[0]))["request_id"] == request_id
    finished = [r for r in caplog.records if r.getMessage() == "request finished"]
    assert [getattr(r, "status_code", None) for r in finished] == [500]


def test_the_app_reads_the_synonym_list_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # #121 review R1-2: a missing or broken list shows up as a warning when the API starts,
    # not on the first customer search, and the app still starts (search goes on without
    # query expansion).
    monkeypatch.setattr(synonyms_module, "DEFAULT_PATH", tmp_path / "missing.md")
    synonyms_module.default_query_synonyms.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="martom.search.synonyms"):
            create_app(Settings(), health_checks={"postgres": healthy, "redis": healthy})
    finally:
        synonyms_module.default_query_synonyms.cache_clear()

    assert "synonym list not loaded" in caplog.text
