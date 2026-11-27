"""Run the retrieval eval set against keyword search and print recall@5, recall@10 and MRR.

Needs a database with the extracted catalogue (`martom sync`, then `martom extract`):

    uv run martom migrate
    uv run python scripts/eval_retrieval.py                 # evals/retrieval.jsonl
    uv run python scripts/eval_retrieval.py --set other.jsonl --limit 20

The database comes from DATABASE_URL (default: the local compose database). No API call is
made: keyword search is plain SQL.
"""

import argparse
import asyncio
from pathlib import Path

import psycopg

from martom.evals.retrieval import load_questions, run_keyword_eval, score_run
from martom.settings import Settings

DEFAULT_SET = Path(__file__).resolve().parents[1] / "evals" / "retrieval.jsonl"


async def main(eval_set: Path, limit: int) -> None:
    questions = load_questions(eval_set)
    async with await psycopg.AsyncConnection.connect(Settings().database_url) as conn:
        results = await run_keyword_eval(conn, questions, limit=limit)
    scores = score_run(questions, results)

    print(f"{'question':<32} {'R@5':>5} {'R@10':>5} {'RR':>5}  top 3 ids")
    for question in questions:
        top = ", ".join(str(i) for i in results[question.id][:3]) or "-"
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the retrieval eval set against keyword search."
    )
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, help="JSONL eval set")
    parser.add_argument("--limit", type=int, default=10, help="results per question")
    args = parser.parse_args()
    asyncio.run(main(args.set, args.limit))
