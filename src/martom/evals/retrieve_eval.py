"""The M3-4 retrieval eval: recall and latency of retrieve() (parse -> hybrid -> rerank).

scripts/eval_retrieve.py prints the numbers; the metrics themselves (recall@k, MRR) come
from martom.evals.retrieval, unchanged.
"""

import math
from collections.abc import Sequence

import psycopg

from martom.embeddings import Embedder
from martom.evals.retrieval import EvalQuestion, run_eval
from martom.search.query_parser import QueryParser
from martom.search.rerank import Reranker
from martom.search.retrieve import Retrieval, retrieve


def percentile(samples: Sequence[float], p: float) -> float:
    """The nearest-rank percentile: the smallest sample with at least p% of samples <= it.

    With few samples p95 is simply one of the slowest ones (with 3 samples, the slowest);
    no interpolation pretends to more precision than the data has.
    """
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


async def run_retrieve_eval(
    conn: psycopg.AsyncConnection,
    questions: Sequence[EvalQuestion],
    *,
    embedder: Embedder,
    parser: QueryParser | None,
    reranker: Reranker | None,
    limit: int = 10,
) -> tuple[dict[str, list[int]], dict[str, Retrieval]]:
    """Every question through retrieve(fallback=False): (id -> ranked ids, id -> Retrieval).

    fallback=False: a failed parse, embedding or rerank stops the eval instead of quietly
    scoring a degraded answer.
    """
    details: dict[str, Retrieval] = {}
    by_text = {q.question: q.id for q in questions}

    async def search(text: str) -> list[int]:
        result = await retrieve(
            conn,
            text,
            embedder=embedder,
            parser=parser,
            reranker=reranker,
            limit=limit,
            fallback=False,
        )
        details[by_text[text]] = result
        return [hit.listing_id for hit in result.hits]

    return await run_eval(questions, search), details
