"""Compare embedding models on the retrieval eval set (M3-3), for choosing one.

For each model: embed the catalogue and the knowledge chunks into the database (`martom
embed`'s job, through a local vector cache), then score keyword, vector and hybrid search
on the eval set, with the forbidden hit rate and the #102 cases from the same run
(seed-12 'kolica' and seed-14 'građevinska kolica' must not return the grinder, seed-13
'panelan' must still find the panels). Prints two tables at the end.

    uv sync --group local-embeddings     # the local models need sentence-transformers
    COHERE_API_KEY=... uv run python scripts/compare_embeddings.py
    uv run python scripts/compare_embeddings.py --models local:BAAI/bge-m3

Vectors are cached in data/embedding-cache/ (git-ignored, derived data), so a second run
makes no API call for texts already embedded. The database keeps the LAST model's vectors:
afterwards run `martom embed` with the chosen EMBEDDING_PROVIDER / EMBEDDING_MODEL.
The database comes from DATABASE_URL (default: the local compose database).
"""

import argparse
import asyncio
import re
import time
from dataclasses import replace
from pathlib import Path

import psycopg

from martom.embed_job import run_embedding
from martom.embeddings import FileCachedEmbeddings, make_embedder
from martom.evals.retrieval import (
    ISSUE_102_CASES,
    RunScores,
    case_status,
    load_questions,
    run_search_eval,
    score_run,
)
from martom.settings import Settings

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SET = REPO / "evals" / "retrieval.jsonl"
CACHE_DIR = REPO / "data" / "embedding-cache"
DEFAULT_MODELS = (
    "cohere:embed-multilingual-v3.0",
    "local:intfloat/multilingual-e5-large",
    "local:BAAI/bge-m3",
)


def forbidden_rate(scores: RunScores) -> str:
    rate = scores.forbidden_hit_rate
    return "-" if rate is None else f"{rate:.2f}"


def case_cells(scores: RunScores) -> str:
    return "".join(f" {case_status(scores, case):<22}" for case in ISSUE_102_CASES)


async def compare(models: list[str], eval_set: Path) -> None:
    questions = load_questions(eval_set)
    base = Settings()
    rows: list[str] = []
    case_rows: list[str] = []
    async with await psycopg.AsyncConnection.connect(base.database_url, autocommit=True) as conn:
        keyword = score_run(questions, await run_search_eval(conn, questions, "keyword"))
        rows.append(
            f"{'keyword (baseline)':<40} {'-':>6} {'-':>6} {'-':>6}"
            f" {'-':>6} {keyword.recall_at_5:>6.3f} {keyword.recall_at_10:>6.3f}"
            f" {keyword.mrr:>6.3f} {forbidden_rate(keyword):>6}"
        )
        case_rows.append(f"{'keyword (baseline)':<40}{case_cells(keyword)}")
        for spec in models:
            provider, _, model = spec.partition(":")
            embedder = make_embedder(
                base.model_copy(update={"embedding_provider": provider, "embedding_model": model})
            )
            cache = CACHE_DIR / (re.sub(r"[^a-z0-9]+", "-", embedder.model.lower()) + ".json")
            embedder = replace(
                embedder, embeddings=FileCachedEmbeddings(embedder.embeddings, cache)
            )
            started = time.monotonic()
            stats = await run_embedding(conn, embedder)
            print(
                f"{embedder.model}: embedded {stats.products_embedded} products and "
                f"{stats.chunks_embedded} chunks in {time.monotonic() - started:.0f} s"
            )
            vector = score_run(
                questions, await run_search_eval(conn, questions, "vector", embedder=embedder)
            )
            hybrid = score_run(
                questions, await run_search_eval(conn, questions, "hybrid", embedder=embedder)
            )
            rows.append(
                f"{embedder.model:<40} {vector.recall_at_5:>6.3f} {vector.recall_at_10:>6.3f}"
                f" {vector.mrr:>6.3f} {forbidden_rate(vector):>6}"
                f" {hybrid.recall_at_5:>6.3f} {hybrid.recall_at_10:>6.3f}"
                f" {hybrid.mrr:>6.3f} {forbidden_rate(hybrid):>6}"
            )
            case_rows.append(f"{embedder.model:<40}{case_cells(hybrid)}")
            for question in questions:
                score = hybrid.per_question.get(question.id)
                if score is not None and score.recall_at_10 < 1:
                    print(f"  hybrid misses part of {question.id} (R@10 {score.recall_at_10:.2f})")
    print()
    print(f"{'':<40} {'vector':^27} {'hybrid (or keyword)':^27}")
    print(f"{'model':<40} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'forbid':>6}"
          f" {'R@5':>6} {'R@10':>6} {'MRR':>6} {'forbid':>6}")  # fmt: skip
    for row in rows:
        print(row)
    print()
    print(
        f"{'#102 cases, hybrid (or keyword)':<40}" + "".join(f" {c:<22}" for c in ISSUE_102_CASES)
    )
    for row in case_rows:
        print(row)
    print("forbid = forbidden hit rate (lower is better); FORBIDDEN = a forbidden id in the top 10")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare embedding models on the eval set.")
    parser.add_argument(
        "--models",
        nargs="+",
        default=list(DEFAULT_MODELS),
        help="provider:model, e.g. cohere:embed-multilingual-v3.0 local:BAAI/bge-m3",
    )
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, help="JSONL eval set")
    args = parser.parse_args()
    asyncio.run(compare(args.models, args.set))
