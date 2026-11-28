from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import SecretStr

from martom.agent import factory
from martom.agent.factory import make_agent_models
from martom.settings import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_no_api_key_means_no_advisor() -> None:
    assert make_agent_models(settings(anthropic_api_key=None)) is None
    assert make_agent_models(settings(anthropic_api_key=SecretStr("  "))) is None


def test_models_use_the_configured_ids_without_calling_the_api() -> None:
    models = make_agent_models(
        settings(anthropic_api_key=SecretStr("sk-ant-fake"), agent_model="claude-sonnet-5-5")
    )
    assert models is not None
    assert models.chat_model_name == "claude-sonnet-5-5"
    assert models.guard_model_name == "claude-haiku-5-5"
    assert models.chat.model == "claude-sonnet-5-5"  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_the_retriever_borrows_a_connection_and_passes_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    borrowed: list[object] = []

    class FakePool:
        @asynccontextmanager
        async def connection(self) -> AsyncGenerator[object]:
            conn = object()
            borrowed.append(conn)
            yield conn

    async def fake_retrieve(conn: object, question: str, **kwargs: Any) -> str:
        calls.append({"conn": conn, "question": question, **kwargs})
        return "retrieval"

    monkeypatch.setattr(factory, "retrieve", fake_retrieve)
    monkeypatch.setattr(factory, "borrow", lambda pool: pool.connection())
    cache = object()
    run = factory.make_retriever(
        FakePool(),  # type: ignore[arg-type]
        embedder="embedder",  # type: ignore[arg-type]
        parser=None,
        reranker=None,
        cache=cache,  # type: ignore[arg-type]
    )
    assert await run("panel ograda") == "retrieval"
    assert await run("stup") == "retrieval"
    # A fresh connection per search, and the one search cache of the process (#17).
    assert [call["conn"] for call in calls] == borrowed
    assert len(set(map(id, borrowed))) == 2
    assert all(call["cache"] is cache for call in calls)
    assert calls[0]["question"] == "panel ograda"
