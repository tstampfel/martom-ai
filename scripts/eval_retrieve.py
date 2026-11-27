"""Score retrieve() (M3-4) on the eval set and measure its latency. Run by hand, never in CI.

Three searches are scored side by side on evals/retrieval.jsonl:

  hybrid   hybrid search alone (M3-3), the baseline
  rerank   hybrid top 30 -> Cohere rerank, no query parsing
  full     Haiku filters -> hybrid top 30 -> Cohere rerank (what live search runs)

then `full` is timed as live search runs it (live timeouts and fallbacks on), --repeat
times per question, and p50 / p95 are printed per stage. Last, the #102 cases ('kolica'
must not return the grinder, 'panelan' must find the panels) and the off-topic question's
best rerank score.

Needs a database with the catalogue and its embeddings, COHERE_API_KEY and
ANTHROPIC_API_KEY. Tomislav only (agents never read .env); uv passes the keys from his
.env, never put a key on the command line:

    DATABASE_URL=postgresql://martom:martom@localhost:5433/martom \\
        uv run --env-file .env python scripts/eval_retrieve.py

A Cohere trial key allows only about 10 rerank calls a minute, so by default the script
pauses --pause seconds before each rerank; with a production key pass --pause 0. A rate
limit hit in the timed run shows up as a rerank fallback in the summary (and its latency
is then not the reranked one).
"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

import psycopg

from martom.embeddings import make_embedder
from martom.evals.retrieval import (
    EvalQuestion,
    issue_102_hits,
    load_questions,
    run_search_eval,
    score_run,
)
from martom.evals.retrieve_eval import percentile, run_retrieve_eval
from martom.search.query_parser import QueryParser, make_query_parser
from martom.search.rerank import Reranker, RerankScore, make_reranker
from martom.search.retrieve import retrieve
from martom.settings import Settings

DEFAULT_SET = Path(__file__).resolve().parents[1] / "evals" / "retrieval.jsonl"
STAGES = ("understand", "search", "rerank", "total")


class PausingReranker:
    """Waits `pause` seconds before each call (trial rate limit). The wait is not timed."""

    def __init__(self, inner: Reranker, pause: float) -> None:
        self.inner = inner
        self.model = inner.model
        self.pause = pause

    async def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        await asyncio.sleep(self.pause)
        return await self.inner.rerank(query, documents)


def print_run(name: str, questions: list[EvalQuestion], results: dict[str, list[int]]) -> None:
    scores = score_run(questions, results)
    print(f"\n== {name}")
    print(f"{'question':<32} {'R@5':>5} {'R@10':>5} {'RR':>5}  top 5 ids")
    for question in questions:
        top = ", ".join(str(i) for i in results[question.id][:5]) or "-"
        score = scores.per_question.get(question.id)
        if score is None:
            print(f"{question.id:<32} {'n/a':>5} {'n/a':>5} {'n/a':>5}  {top}")
        else:
            print(
                f"{question.id:<32} {score.recall_at_5:>5.2f} {score.recall_at_10:>5.2f}"
                f" {score.reciprocal_rank:>5.2f}  {top}"
            )
    print(
        f"scored {scores.scored} questions (one question = {1 / scores.scored:.0%} of recall): "
        f"recall@5 {scores.recall_at_5:.3f}  recall@10 {scores.recall_at_10:.3f}  "
        f"MRR {scores.mrr:.3f}"
    )


async def timed_run(
    conn: psycopg.AsyncConnection,
    questions: list[EvalQuestion],
    settings: Settings,
    repeat: int,
    pause: float,
) -> None:
    embedder = make_embedder(settings, for_queries=True)
    parser = make_query_parser(settings)
    reranker = make_reranker(settings, for_queries=True)
    samples: dict[str, list[float]] = {stage: [] for stage in STAGES}
    not_parsed = not_reranked = runs = 0
    # One untimed warm-up: the first call of each client opens its connections.
    await retrieve(conn, questions[0].question, embedder=embedder, parser=parser, reranker=reranker)
    for _ in range(repeat):
        for question in questions:
            await asyncio.sleep(pause)
            result = await retrieve(
                conn, question.question, embedder=embedder, parser=parser, reranker=reranker
            )
            runs += 1
            not_parsed += not result.parsed
            not_reranked += not result.reranked
            for stage in STAGES:
                samples[stage].append(result.timings_ms[stage])
    print(f"\n== latency of `full` as live search runs it ({runs} searches)")
    for stage in STAGES:
        values = samples[stage]
        print(
            f"{stage:<11} p50 {percentile(values, 50):>7.1f} ms"
            f"   p95 {percentile(values, 95):>7.1f} ms"
            f"   max {max(values):>7.1f} ms"
        )
    print(f"fallbacks: parse {not_parsed}/{runs}, rerank {not_reranked}/{runs}")


async def main(eval_set: Path, repeat: int, pause: float) -> None:
    questions = load_questions(eval_set)
    settings = Settings()
    embedder = make_embedder(settings)
    parser: QueryParser | None = make_query_parser(settings)
    reranker = make_reranker(settings)
    if parser is None or reranker is None:
        raise SystemExit("needs ANTHROPIC_API_KEY and COHERE_API_KEY (Tomislav runs this)")
    pausing = PausingReranker(reranker, pause)
    print(f"embeddings {embedder.model}, parser {parser.model_name}, rerank {reranker.model}")
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        hybrid = await run_search_eval(conn, questions, "hybrid", embedder=embedder)
        print_run("hybrid (M3-3 baseline)", questions, hybrid)

        reranked, _ = await run_retrieve_eval(
            conn, questions, embedder=embedder, parser=None, reranker=pausing
        )
        print_run("rerank (hybrid top 30 -> Cohere, no filters)", questions, reranked)
        full, details = await run_retrieve_eval(
            conn, questions, embedder=embedder, parser=parser, reranker=pausing
        )
        print_run("full (Haiku filters -> hybrid top 30 -> Cohere)", questions, full)
        print("\nfilters per question:")
        for question in questions:
            d = details[question.id]
            top = d.hits[0].relevance if d.hits else None
            dropped = " (dropped: matched nothing)" if d.filters_dropped else ""
            if d.unfiltered_added:
                dropped += f" (+{d.unfiltered_added} unfiltered)"
            print(f"  {question.id:<32} {d.filters}{dropped}  best relevance {top}")

        kolica, panelan = await issue_102_hits(conn, embedder)
        print(f"\n#102 hybrid: 'kolica' top 5 {kolica[:5]}, 'panelan' top 5 {panelan[:5]}")
        for text in ("kolica", "panelan"):
            await asyncio.sleep(pause)
            result = await retrieve(
                conn, text, embedder=embedder, parser=parser, reranker=reranker, fallback=False
            )
            top = [(h.listing_id, round(h.relevance or 0, 3)) for h in result.hits[:5]]
            print(f"#102 full:   {text!r} top 5 (id, relevance) {top}")

        await timed_run(conn, questions, settings, repeat, pause)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score and time retrieve() on the eval set.")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, help="JSONL eval set")
    parser.add_argument("--repeat", type=int, default=3, help="timed runs per question")
    parser.add_argument(
        "--pause", type=float, default=6.5, help="seconds before each rerank (trial key: 6.5)"
    )
    args = parser.parse_args()
    asyncio.run(main(args.set, args.repeat, args.pause))
