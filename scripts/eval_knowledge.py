"""Run a knowledge eval set against knowledge search and print recall@5, recall@10 and MRR.

Needs the knowledge chunks in the database with embeddings of the configured model:

    uv run martom migrate
    uv run martom load-knowledge
    uv run martom embed                                   # EMBEDDING_PROVIDER, e.g. cohere
    uv run python scripts/eval_knowledge.py               # evals/knowledge_3d_panels.jsonl
    uv run python scripts/eval_knowledge.py --set other.jsonl

The database comes from DATABASE_URL (default: the local compose database). Each question is
embedded once (one embedding call per question with EMBEDDING_PROVIDER=cohere).
"""

import argparse
import asyncio
from pathlib import Path

import psycopg

from martom.embeddings import make_embedder
from martom.evals.knowledge_eval import (
    KnowledgeCase,
    KnowledgeScores,
    load_knowledge_cases,
    run_knowledge_eval,
    score_knowledge_run,
)
from martom.knowledge import read_chunks
from martom.settings import Settings

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SET = REPO / "evals" / "knowledge_3d_panels.jsonl"


def print_scores(
    cases: list[KnowledgeCase], results: dict[str, list[str]], scores: KnowledgeScores
) -> None:
    print(f"{'case':<34} {'R@5':>5} {'R@10':>5} {'RR':>5}  top 3 chunks")
    for item in cases:
        score = scores.per_case[item.id]
        top = ", ".join(results[item.id][:3]) or "-"
        print(
            f"{item.id:<34} {score.recall_at_5:>5.2f} {score.recall_at_10:>5.2f}"
            f" {score.reciprocal_rank:>5.2f}  {top}"
        )
    print()
    unapproved = sum(1 for item in cases if not item.approved_by)
    print(f"cases: {len(cases)} ({unapproved} not yet approved)")
    print(f"recall@5  {scores.recall_at_5:.3f}")
    print(f"recall@10 {scores.recall_at_10:.3f}")
    print(f"MRR       {scores.mrr:.3f}")


async def main(eval_set: Path, limit: int) -> None:
    chunk_ids = {chunk.id for chunk in read_chunks(REPO / "knowledge")}
    cases = load_knowledge_cases(eval_set, known_chunk_ids=chunk_ids)
    settings = Settings()
    embedder = make_embedder(settings)
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        results = await run_knowledge_eval(conn, cases, embedder=embedder, limit=limit)
    print(f"knowledge search ({embedder.model})")
    print_scores(cases, results, score_knowledge_run(cases, results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a knowledge eval set.")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, help="JSONL eval set")
    parser.add_argument("--limit", type=int, default=10, help="chunks per question")
    args = parser.parse_args()
    if args.limit < 10:
        # recall@10 over fewer than 10 results would be recall@limit under another name.
        parser.error("--limit must be at least 10, or recall@10 means nothing")
    asyncio.run(main(args.set, args.limit))
