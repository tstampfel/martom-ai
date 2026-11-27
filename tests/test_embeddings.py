"""The embedder wrapper (M3-3): batching, dimension checks, the change hash, the file cache.

No network: every test uses a fake LangChain `Embeddings`.
"""

import json
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings

from martom.embeddings import (
    EMBEDDING_DIMENSIONS,
    Embedder,
    EmbeddingError,
    FileCachedEmbeddings,
    make_embedder,
    text_hash,
)
from martom.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class CountingEmbeddings(Embeddings):
    """Returns a vector whose first value is the text's length; records every call."""

    def __init__(self, size: int = EMBEDDING_DIMENSIONS) -> None:
        self.size = size
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def _vector(self, text: str) -> list[float]:
        return [float(len(text))] + [0.0] * (self.size - 1)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self._vector(text)


async def test_documents_are_embedded_in_batches_in_order() -> None:
    fake = CountingEmbeddings()
    embedder = Embedder(model="test/counting", embeddings=fake, batch_size=2)
    vectors = await embedder.embed_documents(["a", "bb", "ccc", "dddd", "eeeee"])
    assert [v[0] for v in vectors] == [1, 2, 3, 4, 5]
    assert fake.document_calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]


async def test_no_documents_means_no_call() -> None:
    fake = CountingEmbeddings()
    assert await Embedder(model="test/counting", embeddings=fake).embed_documents([]) == []
    assert fake.document_calls == []


async def test_a_vector_of_the_wrong_size_is_an_error() -> None:
    # The database column is vector(1024): a model with another size must fail loudly here,
    # not with a database error halfway through a run.
    embedder = Embedder(model="test/small", embeddings=CountingEmbeddings(size=8))
    with pytest.raises(EmbeddingError, match="1024"):
        await embedder.embed_documents(["a"])
    with pytest.raises(EmbeddingError, match="1024"):
        await embedder.embed_query("a")


async def test_a_query_uses_the_query_call() -> None:
    fake = CountingEmbeddings()
    vector = await Embedder(model="test/counting", embeddings=fake).embed_query("abc")
    assert vector[0] == 3
    assert fake.query_calls == ["abc"]


def test_text_hash_changes_with_the_text_and_the_model() -> None:
    base = text_hash("cohere/a", "panelna ograda")
    assert base == text_hash("cohere/a", "panelna ograda")
    assert base != text_hash("cohere/a", "panelna ograda 153")
    assert base != text_hash("cohere/b", "panelna ograda")
    assert len(base) == 64  # sha256 hex


def test_fake_provider_needs_no_key_and_has_the_right_size() -> None:
    embedder = make_embedder(Settings(embedding_provider="fake"))
    assert embedder.model == "fake/deterministic"
    assert len(embedder.embeddings.embed_query("x")) == EMBEDDING_DIMENSIONS


def test_no_provider_is_an_error_naming_the_env_var() -> None:
    with pytest.raises(EmbeddingError, match="EMBEDDING_PROVIDER"):
        make_embedder(Settings(embedding_provider=None))


def test_file_cache_embeds_each_text_once(tmp_path: Path) -> None:
    cache_file = tmp_path / "cache.json"
    fake = CountingEmbeddings()
    cached = FileCachedEmbeddings(fake, cache_file)
    assert [v[0] for v in cached.embed_documents(["a", "bb"])] == [1, 2]
    assert [v[0] for v in cached.embed_documents(["bb", "ccc"])] == [2, 3]
    assert fake.document_calls == [["a", "bb"], ["ccc"]]
    # Queries are cached too, separately from documents (some models embed them differently).
    cached.embed_query("a")
    cached.embed_query("a")
    assert fake.query_calls == ["a"]

    # A new instance reads the saved file: nothing is embedded again.
    again = CountingEmbeddings()
    reread = FileCachedEmbeddings(again, cache_file)
    assert [v[0] for v in reread.embed_documents(["a", "ccc"])] == [1, 3]
    reread.embed_query("a")
    assert again.document_calls == [] and again.query_calls == []
    # The file holds hashes and vectors, never the texts themselves.
    assert "ccc" not in json.dumps(json.loads(cache_file.read_text()))


def test_cohere_needs_its_key() -> None:
    with pytest.raises(EmbeddingError, match="COHERE_API_KEY"):
        make_embedder(Settings(embedding_provider="cohere", cohere_api_key=None))


def test_cohere_uses_the_multilingual_model_by_default() -> None:
    # Building the client makes no network call.
    embedder = make_embedder(
        Settings(embedding_provider="cohere", embedding_model=None, cohere_api_key="fake-key")
    )
    assert embedder.model == "cohere/embed-multilingual-v3.0"
    assert embedder.embeddings.model == "embed-multilingual-v3.0"


def test_an_embedding_model_can_be_named() -> None:
    embedder = make_embedder(
        Settings(
            embedding_provider="cohere",
            embedding_model="embed-multilingual-light-v3.0",
            cohere_api_key="fake-key",
        )
    )
    assert embedder.model == "cohere/embed-multilingual-light-v3.0"


class StubSentenceModel:
    """Stands in for a sentence-transformers model: records what it was asked to encode."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        assert kwargs.get("normalize_embeddings") is True
        self.calls.append(list(texts))
        return [[0.5] * EMBEDDING_DIMENSIONS for _ in texts]


def test_local_e5_adds_its_query_and_passage_prefixes() -> None:
    embedder = make_embedder(Settings(embedding_provider="local", embedding_model=None))
    assert embedder.model == "local/intfloat/multilingual-e5-large"
    stub = StubSentenceModel()
    embedder.embeddings.model = stub  # nothing is downloaded until the first embed call
    embedder.embeddings.embed_documents(["panel"])
    embedder.embeddings.embed_query("ograda")
    assert stub.calls == [["passage: panel"], ["query: ograda"]]


def test_local_bge_m3_needs_no_prefix() -> None:
    embedder = make_embedder(Settings(embedding_provider="local", embedding_model="BAAI/bge-m3"))
    stub = StubSentenceModel()
    embedder.embeddings.model = stub
    embedder.embeddings.embed_documents(["panel"])
    embedder.embeddings.embed_query("ograda")
    assert stub.calls == [["panel"], ["ograda"]]


def test_an_unknown_local_model_is_an_error() -> None:
    # Each local model needs its prefixes known, or its vectors are quietly worse.
    with pytest.raises(EmbeddingError, match="intfloat/multilingual-e5-large"):
        make_embedder(Settings(embedding_provider="local", embedding_model="some/model"))
