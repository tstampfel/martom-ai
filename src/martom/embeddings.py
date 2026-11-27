"""Text embeddings (M3-3): one small wrapper around a LangChain `Embeddings` model.

An embedding is a list of numbers (a vector) that places a text in "meaning space": texts
about the same thing get vectors that point the same way, even when they share no word
("kolica" and "tačke" for a wheelbarrow). Hybrid search compares the query's vector with
each product's vector (pgvector, migration 007) next to keyword search.

Every model goes through `Embedder`, so search and `martom embed` never care which
provider is behind it:

- `model` is the name stored next to each vector (`product.embedding_model`), so search
  only compares vectors made by the same model, and a model change re-embeds every row.
- Every vector must have `EMBEDDING_DIMENSIONS` numbers, the size of the database column.
- Documents are sent in batches (providers cap the texts per request).

`FileCachedEmbeddings` keeps vectors in a local JSON file, so comparing models on the eval
set doesn't pay for the same texts twice (scripts/compare_embeddings.py).
"""

import asyncio
import hashlib
import json
import math
import os
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from langchain_core.embeddings import Embeddings

from martom.settings import Settings

# The size of the vector(...) columns in migration 007. Every allowed model below returns
# exactly this many numbers, so switching between them never needs a migration; a model
# with another size is refused in make_embedder, before any paid call.
EMBEDDING_DIMENSIONS = 1024
# Texts per request. Cohere accepts at most 96 per call; the others accept more.
DEFAULT_BATCH_SIZE = 96

# Cohere models that return 1024 numbers without a dimensions option (langchain-cohere
# passes none). embed-multilingual-light-v3.0, for example, returns 384.
COHERE_MODELS = frozenset({"embed-multilingual-v3.0"})
DEFAULT_COHERE_MODEL = "embed-multilingual-v3.0"  # 100+ languages
# Batch runs (`martom embed`) wait out Cohere's trial rate limit: up to 6 attempts,
# langchain-cohere waiting 4-10 s between them, and 60 s per request so a stalled
# connection can't hang the run.
COHERE_BATCH_MAX_RETRIES = 6
COHERE_BATCH_TIMEOUT_SECONDS = 60.0
# A search query gets one attempt of at most 3 s: langchain-cohere's shortest wait before a
# retry is 4 s, more than a search should spend, and hybrid search answers with keyword
# results when the query can't be embedded (search/hybrid.py).
COHERE_QUERY_MAX_RETRIES = 1
COHERE_QUERY_TIMEOUT_SECONDS = 3.0

# Open multilingual models that run on this machine and give 1024 numbers, with the text
# each one expects before a query and before a document. e5 was trained with "query: " and
# "passage: " in front and works worse without them; bge-m3 uses none.
LOCAL_MODELS: dict[str, tuple[str, str]] = {
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
    "BAAI/bge-m3": ("", ""),
}
DEFAULT_LOCAL_MODEL = "intfloat/multilingual-e5-large"


class EmbeddingError(Exception):
    """The embedding model is not configured, or returned something unusable."""


def text_hash(model: str, text: str) -> str:
    """Change detection for a stored vector: sha256 of the model name and the text.

    `martom embed` re-embeds a row only when this differs from the row's stored hash, so an
    unchanged text costs nothing and a model change re-embeds everything.
    """
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text form, "[0.1,0.2,...]", for a parameter cast with ::vector in SQL."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _check_size(vector: Sequence[float], model: str) -> list[float]:
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise EmbeddingError(
            f"{model} returned a vector of {len(vector)} numbers; the database column "
            f"holds {EMBEDDING_DIMENSIONS}"
        )
    return [float(x) for x in vector]


@dataclass(frozen=True)
class Embedder:
    """A LangChain embeddings model plus the name its vectors are stored under."""

    model: str  # e.g. "cohere/embed-multilingual-v3.0"
    embeddings: Embeddings
    batch_size: int = DEFAULT_BATCH_SIZE

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the same order, sent `batch_size` texts at a time."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            result = await self.embeddings.aembed_documents(batch)
            if len(result) != len(batch):
                raise EmbeddingError(
                    f"{self.model} returned {len(result)} vectors for {len(batch)} texts"
                )
            vectors.extend(_check_size(v, self.model) for v in result)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """The vector for a search query (some models embed queries differently)."""
        return _check_size(await self.embeddings.aembed_query(text), self.model)


def make_embedder(settings: Settings, *, for_queries: bool = False) -> Embedder:
    """The embedder named by EMBEDDING_PROVIDER / EMBEDDING_MODEL.

    `for_queries=True` is for live search (the API, the agent): it gives up fast, so a
    provider problem costs a search seconds, not a minute. The default is for batch runs.

    "fake" gives deterministic random vectors with no network call: they carry no meaning,
    so use it only to try the commands locally, never to judge search quality.
    """
    provider = settings.embedding_provider
    if provider is None:
        raise EmbeddingError("EMBEDDING_PROVIDER is not set (use 'fake' to try it locally)")
    if provider == "fake":
        return Embedder(model="fake/deterministic", embeddings=HashFakeEmbeddings())
    if provider == "cohere":
        if settings.cohere_api_key is None:
            raise EmbeddingError("EMBEDDING_PROVIDER=cohere needs COHERE_API_KEY")
        # Imported here so the other providers don't load the Cohere SDK.
        from langchain_cohere import CohereEmbeddings

        model = settings.embedding_model or DEFAULT_COHERE_MODEL
        if model not in COHERE_MODELS:
            raise EmbeddingError(
                f"Cohere model {model!r} doesn't return {EMBEDDING_DIMENSIONS} numbers; "
                f"allowed: {', '.join(sorted(COHERE_MODELS))}"
            )
        # langchain-cohere sends input_type "search_document" for documents and
        # "search_query" for queries, which Cohere's v3 models need.
        cohere = CohereEmbeddings(
            model=model,
            cohere_api_key=settings.cohere_api_key,
            max_retries=COHERE_QUERY_MAX_RETRIES if for_queries else COHERE_BATCH_MAX_RETRIES,
            request_timeout=(
                COHERE_QUERY_TIMEOUT_SECONDS if for_queries else COHERE_BATCH_TIMEOUT_SECONDS
            ),
        )
        return Embedder(model=f"cohere/{model}", embeddings=cohere)
    if provider == "local":
        model = settings.embedding_model or DEFAULT_LOCAL_MODEL
        if model not in LOCAL_MODELS:
            raise EmbeddingError(f"unknown local model {model!r}; known: {', '.join(LOCAL_MODELS)}")
        query_prefix, document_prefix = LOCAL_MODELS[model]
        return Embedder(
            model=f"local/{model}",
            embeddings=LocalSentenceEmbeddings(model, query_prefix, document_prefix),
            batch_size=32,  # one forward pass on this machine, not an API request
        )
    raise EmbeddingError(f"unknown EMBEDDING_PROVIDER {provider!r}")


class LocalSentenceEmbeddings(Embeddings):
    """An open sentence-transformers model run on this machine (no key, no network after the
    first download). The model loads on the first embed call, so building one is cheap.

    Needs the optional dependency group: `uv sync --group local-embeddings` (it pulls in
    PyTorch, so it is not part of the default install or the production image).
    """

    def __init__(self, name: str, query_prefix: str, document_prefix: str) -> None:
        self.name = name
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self.model: object | None = None  # a SentenceTransformer once loaded

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if self.model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "EMBEDDING_PROVIDER=local needs `uv sync --group local-embeddings`"
                ) from exc
            self.model = SentenceTransformer(self.name)
        # normalize_embeddings: unit-length vectors, so cosine distance behaves the same for
        # every model.
        vectors = self.model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
        return [[float(x) for x in vector] for vector in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode([self.document_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self.query_prefix + text])[0]


class HashFakeEmbeddings(Embeddings):
    """Deterministic unit vectors seeded by each text's sha256: no network, no meaning.

    LangChain's own DeterministicFakeEmbedding needs numpy, which nothing else here uses.
    """

    def _vector(self, text: str) -> list[float]:
        rng = random.Random(hashlib.sha256(text.encode()).digest())
        vector = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIMENSIONS)]
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FileCachedEmbeddings(Embeddings):
    """Wraps an `Embeddings` model and keeps every vector it returns in a JSON file.

    The file maps sha256(text) to the vector, separately for documents and queries (some
    models embed the two differently), and never stores the text itself. Keep it under
    /data/ (git-ignored): it is derived from the catalogue.
    """

    def __init__(self, inner: Embeddings, path: Path) -> None:
        self.inner = inner
        self.path = path
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self._documents: dict[str, list[float]] = saved.get("documents", {})
        self._queries: dict[str, list[float]] = saved.get("queries", {})

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _save(self) -> None:
        # Write a temporary file next to it, then swap it in: os.replace is atomic, so a
        # Ctrl-C or a killed process leaves the old file or the new one, never half of one.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"documents": self._documents, "queries": self._queries}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, self.path)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # dict.fromkeys keeps the first-seen order and drops repeats, like a JS Set.
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in self._documents]
        if missing:
            for text, vector in zip(missing, self.inner.embed_documents(missing), strict=True):
                self._documents[self._key(text)] = vector
            self._save()
        return [self._documents[self._key(t)] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        key = self._key(text)
        if key not in self._queries:
            self._queries[key] = self.inner.embed_query(text)
            self._save()
        return self._queries[key]

    # The base class would run the sync methods in a thread anyway; doing it explicitly
    # keeps the cache dict touched from one place at a time per call.
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)
