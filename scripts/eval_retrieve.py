"""Score retrieve() (M3-4) on the eval set and measure its latency. Run by hand, never in CI.

Three searches are scored side by side on evals/retrieval.jsonl:

  hybrid   hybrid search alone (M3-3), the baseline
  rerank   hybrid top 30 -> Cohere rerank, no query parsing
  full     Haiku filters -> hybrid top 30 -> Cohere rerank (what live search runs)

`rerank` and `full` run once per model in --rerank-models (default: RERANK_MODEL), on
the same questions and candidates: Haiku parses each question once and every model sees
those same filters, so only the reranker differs. That parser has no time limit (60 s,
retries), so `full` there assumes the parse never falls back; a second `full` row per
model uses the live parser (PARSE_TIMEOUT_SECONDS, unfiltered when slower) and shows its
fallback count, which is what customers get. Then `full` is timed as live search runs it
(live timeouts and fallbacks on, Haiku called each time), --repeat times per question,
the models taking turns on each question so they share network conditions,
and p50 / p95 are printed per stage and model. A summary table puts recall@5, recall@10,
MRR and p95 of each model side by side. Last, the #102 eval cases (seed-12 'kolica' and
seed-14 'građevinska kolica' must not return the grinder, seed-13 'panelan' must find the
panels) in every scored run, from the same eval scores (case_status).

Needs a database with the catalogue and its embeddings, COHERE_API_KEY and
ANTHROPIC_API_KEY. Tomislav only (agents never read .env); uv passes the keys from his
.env, never put a key on the command line:

    DATABASE_URL=postgresql://martom:martom@localhost:5433/martom \\
        uv run --env-file .env python scripts/eval_retrieve.py \\
        --rerank-models rerank-v4.0-fast,rerank-v5.0-fast

A Cohere trial key allows only about 10 rerank calls a minute (per key, across models),
so by default the script pauses --pause seconds before each rerank; with a production key
pass --pause 0. A rate limit hit in the timed run shows up as a rerank fallback in the
summary (and its latency is then not the reranked one). Two models take about twice as
long as one (roughly 15 minutes at the default pause).
"""

import argparse
import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from martom.embeddings import make_embedder
from martom.evals.retrieval import (
    ISSUE_102_CASES,
    EvalId,
    EvalQuestion,
    RunScores,
    case_status,
    load_questions,
    run_search_eval,
    score_run,
)
from martom.evals.retrieve_eval import percentile, run_retrieve_eval
from martom.search.query_parser import (
    PARSE_TIMEOUT_SECONDS,
    ParseResult,
    QueryParser,
    make_query_parser,
)
from martom.search.rerank import Reranker, RerankScore, make_reranker
from martom.search.retrieve import Retrieval, retrieve
from martom.settings import Settings

DEFAULT_SET = Path(__file__).resolve().parents[1] / "evals" / "retrieval.jsonl"
STAGES = ("understand", "parse", "search", "rerank", "total")


class PausingReranker:
    """Waits `pause` seconds before each call (trial rate limit). The wait is not timed."""

    def __init__(self, inner: Reranker, pause: float) -> None:
        self.inner = inner
        self.model = inner.model
        self.pause = pause

    async def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        await asyncio.sleep(self.pause)
        return await self.inner.rerank(query, documents)


def print_run(
    name: str, questions: list[EvalQuestion], results: dict[str, list[EvalId]]
) -> RunScores:
    scores = score_run(questions, results)
    print(f"\n== {name}")
    print(f"{'question':<32} {'R@5':>5} {'R@10':>5} {'RR':>5}  top 5 ids")
    for question in questions:
        top = ", ".join(str(i) for i in results[question.id][:5]) or "-"
        if question.id in scores.forbidden_hit_ids:
            top += "  FORBIDDEN id in top 10"
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
    if scores.forbidden_hit_rate is not None:
        hits = ", ".join(scores.forbidden_hit_ids) or "none"
        print(
            f"forbidden hit rate {scores.forbidden_hit_rate:.3f}"
            f" ({len(scores.forbidden_hit_ids)} of {scores.forbidden_checked}: {hits})"
        )
    return scores


def print_cases(runs: list[tuple[str, RunScores]]) -> None:
    """The #102 eval cases in every scored run: "ok" = full recall@10 and no forbidden id
    in the top 10, else what failed ("-": the eval set has no such question)."""
    width = max(len(name) for name, _ in runs)
    print("\n== #102 cases (from the scores above)")
    print(f"{'run':<{width}}" + "".join(f"  {case:<28}" for case in ISSUE_102_CASES))
    for name, scores in runs:
        cells = "".join(f"  {case_status(scores, case):<28}" for case in ISSUE_102_CASES)
        print(f"{name:<{width}}{cells}")


class CachingParser:
    """Asks the real parser once per question, then repeats its answer, so every rerank
    model is scored with the same filters (Haiku can read a question differently twice).
    Each first call is timed with no time limit: how long Haiku really takes."""

    def __init__(self, inner: QueryParser) -> None:
        self.inner = inner
        self.model_name = inner.model_name
        self._seen: dict[str, ParseResult] = {}
        self.parse_ms: list[float] = []

    async def parse_filters(self, question: str, *, fallback: bool = True) -> ParseResult:
        if question not in self._seen:
            started = time.perf_counter()
            self._seen[question] = await self.inner.parse_filters(question, fallback=fallback)
            self.parse_ms.append((time.perf_counter() - started) * 1000)
        return self._seen[question]


class LiveFallbackParser:
    """The live parser with its fallback always on: retrieve(fallback=False) keeps the
    embedding and rerank strict, while a parse over the live limit gives "no filters", as
    in live search."""

    def __init__(self, inner: QueryParser) -> None:
        self.inner = inner
        self.model_name = inner.model_name

    async def parse_filters(self, question: str, *, fallback: bool = True) -> ParseResult:
        return await self.inner.parse_filters(question, fallback=True)


def print_parse_latency(parse_ms: list[float]) -> None:
    """Haiku's own time per question, without the live limit (scoring runs)."""
    over = sum(ms > PARSE_TIMEOUT_SECONDS * 1000 for ms in parse_ms)
    print(
        f"\nHaiku parse alone, no time limit ({len(parse_ms)} questions): "
        f"p50 {percentile(parse_ms, 50):.0f} ms  p95 {percentile(parse_ms, 95):.0f} ms  "
        f"max {max(parse_ms):.0f} ms; {over}/{len(parse_ms)} over the live limit of "
        f"{PARSE_TIMEOUT_SECONDS} s (live search would run those without filters)"
    )


@dataclass
class ModelResult:
    model: str
    scores: RunScores | None = None  # `full`, parser without time limit
    live_scores: RunScores | None = None  # `full`, live parser (live limit, fallback on)
    live_not_parsed: int = 0
    samples: dict[str, list[float]] = field(default_factory=lambda: {stage: [] for stage in STAGES})
    not_parsed: int = 0
    not_reranked: int = 0


async def timed_run(
    conn: psycopg.AsyncConnection,
    questions: list[EvalQuestion],
    settings: Settings,
    results: list[ModelResult],
    repeat: int,
    pause: float,
) -> None:
    embedder = make_embedder(settings, for_queries=True)
    parser = make_query_parser(settings)
    rerankers = {
        r.model: make_reranker(model_settings(settings, r.model), for_queries=True) for r in results
    }
    # One untimed warm-up per model: the first call of each client opens its connections.
    for reranker in rerankers.values():
        await asyncio.sleep(pause)
        await retrieve(
            conn, questions[0].question, embedder=embedder, parser=parser, reranker=reranker
        )
    for _ in range(repeat):
        for question in questions:
            for r in results:  # the models take turns on each question
                await asyncio.sleep(pause)
                result = await retrieve(
                    conn,
                    question.question,
                    embedder=embedder,
                    parser=parser,
                    reranker=rerankers[r.model],
                )
                r.not_parsed += not result.parsed
                r.not_reranked += not result.reranked
                for stage in STAGES:
                    r.samples[stage].append(result.timings_ms[stage])
    for r in results:
        runs = len(r.samples["total"])
        print(f"\n== latency of `full` with {r.model}, as live search runs it ({runs} searches)")
        for stage in STAGES:
            values = r.samples[stage]
            print(
                f"{stage:<11} p50 {percentile(values, 50):>7.1f} ms"
                f"   p95 {percentile(values, 95):>7.1f} ms"
                f"   max {max(values):>7.1f} ms"
            )
        print(
            f"fallbacks: parse {r.not_parsed}/{runs} (Haiku over {PARSE_TIMEOUT_SECONDS} s or"
            f" failed: those searches ran without filters), rerank {r.not_reranked}/{runs}"
        )


def print_fallbacks(details: dict[str, Retrieval], *, parsed: bool) -> None:
    """Scoring runs use fallback=False, so a failed parse or rerank stops the eval instead
    of being scored; these counts confirm every scored answer was the real one, except in
    the live-parser row, whose parse count is the measured fallback rate."""
    n = len(details)
    not_reranked = sum(not d.reranked for d in details.values())
    parse = f"{sum(not d.parsed for d in details.values())}/{n}" if parsed else "- (no parser)"
    print(f"fallbacks: parse {parse}, rerank {not_reranked}/{n}")


def model_settings(settings: Settings, model: str) -> Settings:
    return settings.model_copy(update={"rerank_model": model})


def print_summary(results: list[ModelResult], n: int) -> None:
    print(
        "\n== rerank models side by side (`full`): recall with the batch parser (no time"
        f" limit) and with the live parser ({PARSE_TIMEOUT_SECONDS} s, unfiltered when slower);"
        " p95 of the live"
        " timed run"
    )
    print(
        f"{'model':<22} {'parser':<16} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'parse fb':>9}"
        f" {'p95 rerank':>11} {'p95 total':>10}"
    )
    for r in results:
        assert r.scores is not None and r.live_scores is not None
        rows = [
            ("batch, no limit", r.scores, "-"),
            (f"live {PARSE_TIMEOUT_SECONDS} s", r.live_scores, f"{r.live_not_parsed}/{n}"),
        ]
        for label, scores, fallbacks in rows:
            print(
                f"{r.model:<22} {label:<16} {scores.recall_at_5:>6.3f} {scores.recall_at_10:>6.3f}"
                f" {scores.mrr:>6.3f} {fallbacks:>9}"
                f" {percentile(r.samples['rerank'], 95):>8.1f} ms"
                f" {percentile(r.samples['total'], 95):>7.1f} ms"
            )
    scored = results[0].scores.scored if results and results[0].scores else 0
    if scored:
        print(
            f"(one question = {1 / scored:.0%} of recall on {scored} scored questions:"
            " a one-question gap is within noise)"
        )


async def main(eval_set: Path, models: list[str], repeat: int, pause: float) -> None:
    questions = load_questions(eval_set)
    settings = Settings()
    embedder = make_embedder(settings)
    # Scoring waits for Haiku (60 s, retries): a slow reply must not stop the eval or be
    # scored as "no filters". The timed run below uses the live parser and its
    # PARSE_TIMEOUT_SECONDS limit.
    batch_parser = make_query_parser(settings, for_queries=False)
    # The "live parser" rows: PARSE_TIMEOUT_SECONDS and no retries, a slow parse runs
    # unfiltered, as in live search. Called afresh for every question, so each row has its
    # own count.
    live_parser = make_query_parser(settings)
    rerankers = {m: make_reranker(model_settings(settings, m)) for m in models}
    if batch_parser is None or live_parser is None or None in rerankers.values():
        raise SystemExit("needs ANTHROPIC_API_KEY and COHERE_API_KEY (Tomislav runs this)")
    parser = CachingParser(batch_parser)
    live = LiveFallbackParser(live_parser)
    print(f"embeddings {embedder.model}, parser {parser.model_name}, rerank {', '.join(models)}")
    results = [ModelResult(m) for m in models]
    # One untimed call on the live client: live search runs on a warm client, so connection
    # setup must not cost the first live-row question its parse (R-A3-1).
    await live_parser.parse_filters(questions[0].question)
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        hybrid = await run_search_eval(conn, questions, "hybrid", embedder=embedder)
        case_runs = [("hybrid", print_run("hybrid (M3-3 baseline)", questions, hybrid))]

        for r in results:
            pausing = PausingReranker(rerankers[r.model], pause)
            reranked, rerank_details = await run_retrieve_eval(
                conn, questions, embedder=embedder, parser=None, reranker=pausing
            )
            rerank_scores = print_run(
                f"rerank with {r.model} (hybrid top 30 -> Cohere, no filters)", questions, reranked
            )
            print_fallbacks(rerank_details, parsed=False)
            full, details = await run_retrieve_eval(
                conn, questions, embedder=embedder, parser=parser, reranker=pausing
            )
            r.scores = batch_scores = print_run(
                f"full with {r.model}, batch parser without time limit"
                " (Haiku filters -> hybrid -> Cohere)",
                questions,
                full,
            )
            print_fallbacks(details, parsed=True)
            live_full, live_details = await run_retrieve_eval(
                conn, questions, embedder=embedder, parser=live, reranker=pausing
            )
            r.live_scores = live_scores = print_run(
                f"full with {r.model}, live parser ({PARSE_TIMEOUT_SECONDS} s, unfiltered"
                " when slower)",
                questions,
                live_full,
            )
            print_fallbacks(live_details, parsed=True)
            r.live_not_parsed = sum(not d.parsed for d in live_details.values())
            case_runs += [
                (f"rerank {r.model}", rerank_scores),
                (f"full {r.model}, batch parser", batch_scores),
                (f"full {r.model}, live parser", live_scores),
            ]
            print(f"\nfilters per question ({r.model}):")
            for question in questions:
                d = details[question.id]
                top = d.hits[0].relevance if d.hits else None
                note = " (no hit passed every filter)" if d.filters_dropped else ""
                if d.top_up_added:
                    matched = sum(h.matched_filters for h in d.hits)
                    note += f" (+{d.top_up_added} top-up; {matched}/{len(d.hits)} hits matched)"
                print(f"  {question.id:<32} {d.filters}{note}  best relevance {top}")

        print_parse_latency(parser.parse_ms)

        print_cases(case_runs)

        await timed_run(conn, questions, settings, results, repeat, pause)
        print_summary(results, len(questions))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score and time retrieve() on the eval set.")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, help="JSONL eval set")
    parser.add_argument(
        "--rerank-models",
        default=None,
        help="comma-separated Cohere rerank models to compare (default: RERANK_MODEL)",
    )
    parser.add_argument("--repeat", type=int, default=3, help="timed runs per question")
    parser.add_argument(
        "--pause", type=float, default=6.5, help="seconds before each rerank (trial key: 6.5)"
    )
    args = parser.parse_args()
    models = (
        [m.strip() for m in args.rerank_models.split(",") if m.strip()]
        if args.rerank_models
        else [Settings().rerank_model]
    )
    asyncio.run(main(args.set, models, args.repeat, args.pause))
