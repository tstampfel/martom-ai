"""Cohere Rerank (M3-4): reorder the hybrid search candidates by relevance to the question.

Hybrid search (search/hybrid.py) ranks by reciprocal rank fusion, which only looks at
positions in two lists. A reranker is a cross-encoder: it reads the question and each
candidate's text together and scores how well that text answers the question (0..1). It is
too slow to run on the whole catalogue, so it only reorders the top 30 hybrid candidates
(search/retrieve.py), and it is skipped when Cohere is down or slow.

The Cohere SDK's async client is used directly rather than langchain-cohere's
`CohereRerank`: that one is synchronous only (it would block the event loop for the length
of a network call) and can't set a per-request retry count.

The key is passed as a plain string (`get_secret_value()`): a pydantic SecretStr turned into
a string on the way prints as "**********", which is what langchain-cohere's embeddings
sent before #15 fixed it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import cohere

from martom.settings import Settings

# Cohere Rerank 5 Fast (docs/PLAN.md, Stack): multilingual, low latency. It tied Rerank 4
# Fast on the eval set (evals/baselines.md, #16) and is newer and cheaper; set
# RERANK_MODEL=rerank-v4.0-fast to go back.
DEFAULT_RERANK_MODEL = "rerank-v5.0-fast"
# Live search: one attempt and a short HTTP timeout; retrieve() also caps the whole wait.
QUERY_MAX_RETRIES = 0
QUERY_TIMEOUT_SECONDS = 2.0
# Evals and scripts: wait out a rate limit (the trial key allows only a few calls a minute).
BATCH_MAX_RETRIES = 3
BATCH_TIMEOUT_SECONDS = 30.0


class RerankError(Exception):
    """The reranker answered something that doesn't fit the documents sent."""


@dataclass(frozen=True)
class RerankScore:
    index: int  # position in the documents sent
    relevance: float  # 0..1, higher = more relevant; not a probability


class Reranker(Protocol):
    """Anything that scores documents for a query (Cohere, or a fake in tests).

    Like a TypeScript interface: any class with this attribute and method fits.
    """

    model: str

    async def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        """One score per document, best first."""
        ...


class CohereReranker:
    def __init__(self, client: cohere.AsyncClientV2, model: str, *, max_retries: int) -> None:
        self.client = client
        self.cohere_model = model
        self.model = f"cohere/{model}"
        self.max_retries = max_retries

    async def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        if not documents:
            return []  # no call for nothing
        response = await self.client.rerank(
            model=self.cohere_model,
            query=query,
            documents=list(documents),
            top_n=len(documents),
            request_options={"max_retries": self.max_retries},
        )
        scores = [RerankScore(r.index, float(r.relevance_score)) for r in response.results]
        if len({s.index for s in scores}) != len(scores) or any(
            not 0 <= s.index < len(documents) for s in scores
        ):
            raise RerankError(f"{self.model} returned indexes that don't fit the documents")
        return sorted(scores, key=lambda s: -s.relevance)


def make_reranker(settings: Settings, *, for_queries: bool = False) -> CohereReranker | None:
    """The Cohere reranker, or None without COHERE_API_KEY (results stay in RRF order).

    `for_queries=True` is for live search: no retries and a short timeout.
    """
    key = settings.cohere_api_key
    if key is None or not key.get_secret_value().strip():
        return None
    client = cohere.AsyncClientV2(
        api_key=key.get_secret_value(),
        timeout=QUERY_TIMEOUT_SECONDS if for_queries else BATCH_TIMEOUT_SECONDS,
    )
    return CohereReranker(
        client,
        settings.rerank_model,
        max_retries=QUERY_MAX_RETRIES if for_queries else BATCH_MAX_RETRIES,
    )
