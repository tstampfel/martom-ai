"""The Cohere rerank client (M3-4). No network: the Cohere client is replaced by a fake."""

from types import SimpleNamespace
from typing import ClassVar

import pytest
from pydantic import SecretStr

from martom.search import rerank
from martom.search.rerank import (
    DEFAULT_RERANK_MODEL,
    CohereReranker,
    RerankError,
    RerankScore,
    make_reranker,
)
from martom.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeAsyncClientV2:
    """Records how it was built and what rerank got; answers with `results`."""

    instances: ClassVar[list["FakeAsyncClientV2"]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls: list[dict] = []
        self.results: list[SimpleNamespace] = []
        FakeAsyncClientV2.instances.append(self)

    async def rerank(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(results=self.results)


@pytest.fixture
def fake_cohere(monkeypatch: pytest.MonkeyPatch) -> type[FakeAsyncClientV2]:
    FakeAsyncClientV2.instances = []
    monkeypatch.setattr(rerank.cohere, "AsyncClientV2", FakeAsyncClientV2)
    return FakeAsyncClientV2


def test_default_model_is_rerank_4_fast() -> None:
    assert DEFAULT_RERANK_MODEL == "rerank-v4.0-fast"
    assert Settings().rerank_model == DEFAULT_RERANK_MODEL


def test_no_key_means_no_reranker(fake_cohere: type[FakeAsyncClientV2]) -> None:
    assert make_reranker(Settings(cohere_api_key=None)) is None
    assert make_reranker(Settings(cohere_api_key=SecretStr(" "))) is None
    assert fake_cohere.instances == []


def test_client_gets_the_unwrapped_key_not_the_mask(fake_cohere: type[FakeAsyncClientV2]) -> None:
    # #15: a SecretStr str()-ed on the way would send "**********" and get a 401.
    reranker = make_reranker(Settings(cohere_api_key=SecretStr("real-cohere-key")))
    assert reranker is not None
    assert fake_cohere.instances[0].kwargs["api_key"] == "real-cohere-key"
    assert reranker.model == "cohere/rerank-v4.0-fast"


def test_live_queries_get_one_attempt_and_a_short_timeout(
    fake_cohere: type[FakeAsyncClientV2],
) -> None:
    live = make_reranker(Settings(cohere_api_key=SecretStr("k")), for_queries=True)
    batch = make_reranker(Settings(cohere_api_key=SecretStr("k")))
    assert isinstance(live, CohereReranker) and isinstance(batch, CohereReranker)
    assert live.max_retries == 0
    assert batch.max_retries > 0
    assert fake_cohere.instances[0].kwargs["timeout"] < fake_cohere.instances[1].kwargs["timeout"]


async def test_rerank_sends_every_document_and_returns_scores_best_first(
    fake_cohere: type[FakeAsyncClientV2],
) -> None:
    reranker = make_reranker(
        Settings(cohere_api_key=SecretStr("k"), rerank_model="rerank-v4.0-pro"), for_queries=True
    )
    assert reranker is not None
    client = fake_cohere.instances[0]
    client.results = [
        SimpleNamespace(index=2, relevance_score=0.9),
        SimpleNamespace(index=0, relevance_score=0.4),
        SimpleNamespace(index=1, relevance_score=0.1),
    ]
    scores = await reranker.rerank("anker m8", ["a", "b", "c"])
    assert scores == [RerankScore(2, 0.9), RerankScore(0, 0.4), RerankScore(1, 0.1)]
    call = client.calls[0]
    assert call["model"] == "rerank-v4.0-pro"
    assert call["query"] == "anker m8"
    assert call["documents"] == ["a", "b", "c"]
    assert call["top_n"] == 3
    assert call["request_options"]["max_retries"] == 0


async def test_rerank_of_nothing_makes_no_call(fake_cohere: type[FakeAsyncClientV2]) -> None:
    reranker = make_reranker(Settings(cohere_api_key=SecretStr("k")))
    assert reranker is not None
    assert await reranker.rerank("x", []) == []
    assert fake_cohere.instances[0].calls == []


async def test_rerank_refuses_an_answer_that_does_not_fit_the_documents(
    fake_cohere: type[FakeAsyncClientV2],
) -> None:
    reranker = make_reranker(Settings(cohere_api_key=SecretStr("k")))
    assert reranker is not None
    fake_cohere.instances[0].results = [SimpleNamespace(index=5, relevance_score=0.9)]
    with pytest.raises(RerankError):
        await reranker.rerank("x", ["a", "b"])
