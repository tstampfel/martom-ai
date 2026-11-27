"""Compare embedding models on the retrieval eval set (M3-3), for choosing one.

For each model: embed the catalogue and the knowledge chunks into the database (`martom
embed`'s job, through a local vector cache), then score keyword, vector and hybrid search
on the eval set, and check the two #102 cases ('kolica' must not return the grinder,
'panelan' must still find the panels). Prints one table at the end.

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
from martom.evals.retrieval import issue_102_hits, load_questions, run_search_eval, score_run
from martom.settings import Settings

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SET = REPO / "evals" / "retrieval.jsonl"
CACHE_DIR = REPO / "data" / "embedding-cache"
DEFAULT_MODELS = (
    "cohere:embed-multilingual-v3.0",
    "local:intfloat/multilingual-e5-large",
    "local:BAAI/bge-m3",
)
# #102: hybrid search must drop the grinder for "kolica" and keep the panels for "panelan".
GRINDER = 52889522
PANELS = {63996799, 1199211}


async def compare(models: list[str], eval_set: Path) -> None:
    questions = load_questions(eval_set)
    base = Settings()
    rows: list[str] = []
    async with await psycopg.AsyncConnection.connect(base.database_url, autocommit=True) as conn:
        keyword = score_run(questions, await run_search_eval(conn, questions, "keyword"))
        rows.append(
            f"{'keyword (baseline)':<40} {'-':>6} {'-':>6} {'-':>6}"
            f" {keyword.recall_at_5:>6.3f} {keyword.recall_at_10:>6.3f} {keyword.mrr:>6.3f}"
            "    -       -"
        )
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
            kolica, panelan = await issue_102_hits(conn, embedder)
            rows.append(
                f"{embedder.model:<40} {vector.recall_at_5:>6.3f} {vector.recall_at_10:>6.3f}"
                f" {vector.mrr:>6.3f} {hybrid.recall_at_5:>6.3f} {hybrid.recall_at_10:>6.3f}"
                f" {hybrid.mrr:>6.3f}"
                f"  {'ok' if GRINDER not in kolica else 'GRINDER':>7}"
                f"  {'ok' if set(panelan) >= PANELS else 'MISSING':>7}"
            )
            for question in questions:
                score = hybrid.per_question.get(question.id)
                if score is not None and score.recall_at_10 < 1:
                    print(f"  hybrid misses part of {question.id} (R@10 {score.recall_at_10:.2f})")
    print()
    print(f"{'':<40} {'vector':^20} {'hybrid (or keyword)':^20}  #102 checks")
    print(f"{'model':<40} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6}"
          "  kolica  panelan")  # fmt: skip
    for row in rows:
        print(row)


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
