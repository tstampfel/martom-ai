"""open_chat_service: the API's real advisor wiring, opened once per process (#21)."""

import logging
from typing import Any

import pytest
from pydantic import SecretStr
from tests.search_catalogue import REPO

from martom.api import chat_service
from martom.api.chat import ChatService
from martom.api.chat_service import open_chat_service
from martom.api.threads import PostgresThreadLocks
from martom.checkpointer import setup_checkpointer
from martom.migrate import run_migrations
from martom.settings import Settings

pytestmark = pytest.mark.anyio

UNREACHABLE = "postgresql://martom:martom@127.0.0.1:1/martom"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


async def test_without_an_anthropic_key_there_is_no_advisor_and_no_database(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="martom.api.chat_service"):
        async with open_chat_service(settings(database_url=UNREACHABLE)) as service:
            assert service is None
    assert "chat disabled" in caplog.text


async def test_without_an_embedder_there_is_no_advisor(caplog: pytest.LogCaptureFixture) -> None:
    config = settings(
        database_url=UNREACHABLE,
        anthropic_api_key=SecretStr("sk-ant-fake"),
        embedding_provider="cohere",
        cohere_api_key=None,
    )
    with caplog.at_level(logging.WARNING, logger="martom.api.chat_service"):
        async with open_chat_service(config) as service:
            assert service is None
    assert "chat disabled" in caplog.text
    assert "sk-ant-fake" not in caplog.text


async def test_the_service_opens_the_graph_with_pools_and_closes_the_cache(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_migrations(database_url, REPO / "migrations")
    setup_checkpointer(database_url)
    closed: list[bool] = []

    class FakeCache:
        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr(chat_service, "make_search_cache", lambda _: FakeCache())
    config = settings(
        database_url=database_url,
        anthropic_api_key=SecretStr("sk-ant-fake"),
        embedding_provider="fake",
    )
    async with open_chat_service(config) as service:
        assert isinstance(service, ChatService)
        assert isinstance(service.locks, PostgresThreadLocks)
        assert await service.thread_exists("x" * 43) is False
        assert closed == []
    assert closed == [True]
