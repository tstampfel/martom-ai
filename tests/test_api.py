"""The FastAPI app: GET /health, the request-id middleware and CORS.

Most tests swap the real Postgres/Redis checks for small fake async functions, so they
run without any server. The last test uses the real checks against the test servers.
"""

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from martom.api.app import create_app
from martom.log import JsonFormatter
from martom.settings import Settings

PAGES_DEV = "https://martom-savjetnik.pages.dev"


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


@pytest.mark.parametrize("down", ["postgres", "redis"])
def test_health_is_503_when_one_dependency_fails(down: str) -> None:
    checks = {"postgres": healthy, "redis": healthy, down: broken}

    response = client_with(**checks).get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"][down] == "error"
    assert sum(result == "ok" for result in body["checks"].values()) == 1


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
