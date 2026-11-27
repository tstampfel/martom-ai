"""Run the retrieval eval set against one search and print recall@5, recall@10 and MRR.

Needs a database with the extracted catalogue (`martom sync`, then `martom extract`), and
for vector or hybrid search its embeddings (`martom embed` with the same EMBEDDING_PROVIDER
and EMBEDDING_MODEL as here):

    uv run martom migrate
    uv run python scripts/eval_retrieval.py                       # keyword, evals/retrieval.jsonl
    uv run python scripts/eval_retrieval.py --search hybrid       # needs EMBEDDING_PROVIDER
    uv run python scripts/eval_retrieval.py --set other.jsonl --limit 20

The database comes from DATABASE_URL (default: the local compose database). Keyword search
makes no API call; vector and hybrid search embed each question once (one embedding call
per question with EMBEDDING_PROVIDER=cohere).
"""

import argparse
import asyncio
from pathlib import Path

import psycopg

from martom.embeddings import Embedder, make_embedder
from martom.evals.retrieval import (
    EvalQuestion,
    RunScores,
    load_questions,
    run_search_eval,
    score_run,
)
from martom.settings import Settings

DEFAULT_SET = Path(__file__).resolve().parents[1] / "evals" / "retrieval.jsonl"
MODES = ("keyword", "vector", "hybrid")


def print_scores(
    questions: list[EvalQuestion], results: dict[str, list[int]], scores: RunScores
) -> None:
    print(f"{'question':<32} {'R@5':>5} {'R@10':>5} {'RR':>5}  top 3 ids")
    for question in questions:
        top = ", ".join(str(i) for i in results[question.id][:3]) or "-"
        if question.id in scores.forbidden_hit_ids:
            top += "  FORBIDDEN id in top 10"
        score = scores.per_question.get(question.id)
        if score is None:
            print(f"{question.id:<32} {'n/a':>5} {'n/a':>5} {'n/a':>5}  {top}  (no expected ids)")
        else:
            print(
                f"{question.id:<32} {score.recall_at_5:>5.2f} {score.recall_at_10:>5.2f}"
                f" {score.reciprocal_rank:>5.2f}  {top}"
            )
    print()
    print(f"questions scored: {scores.scored} (+{len(scores.no_answer_ids)} with no expected ids)")
    print(f"recall@5  {scores.recall_at_5:.3f}")
    print(f"recall@10 {scores.recall_at_10:.3f}")
    print(f"MRR       {scores.mrr:.3f}")
    if scores.forbidden_hit_rate is not None:
        hits = ", ".join(scores.forbidden_hit_ids) or "none"
        print(
            f"forbidden hit rate {scores.forbidden_hit_rate:.3f}"
            f" ({len(scores.forbidden_hit_ids)} of {scores.forbidden_checked}: {hits})"
        )


async def main(eval_set: Path, mode: str, limit: int) -> None:
    questions = load_questions(eval_set)
    settings = Settings()
    embedder: Embedder | None = make_embedder(settings) if mode != "keyword" else None
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        results = await run_search_eval(conn, questions, mode, embedder=embedder, limit=limit)
    print(f"search: {mode}" + (f" ({embedder.model})" if embedder else ""))
    print_scores(questions, results, score_run(questions, results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the retrieval eval set against a search.")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, help="JSONL eval set")
    parser.add_argument("--search", choices=MODES, default="keyword", help="which search")
    parser.add_argument("--limit", type=int, default=10, help="results per question")
    args = parser.parse_args()
    if args.limit < 10:
        # recall@10 over fewer than 10 results would be recall@limit under another name.
        parser.error("--limit must be at least 10, or recall@10 means nothing")
    asyncio.run(main(args.set, args.search, args.limit))
