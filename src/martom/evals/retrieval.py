"""Retrieval metrics for the eval set in `evals/retrieval.jsonl` (docs/PLAN.md, "Evals").

- recall@k: the share of a question's expected listings that appear in the top k results.
  "Did search find what Toni would sell?"
- reciprocal rank: 1 / position of the first expected listing (0 if none is returned).
  MRR is its mean over questions. "How high is the first right answer?"

Questions with no expected ids (off-topic ones such as "Koliko je sati?") have no recall or
reciprocal rank, so they are left out of the averages and listed in `no_answer_ids`; a
later issue (the guardrail, M4) decides what search should return for them.
"""

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

from martom.embeddings import Embedder
from martom.search.hybrid import search_hybrid
from martom.search.keyword import search_keyword
from martom.search.vector import search_vector


@dataclass(frozen=True)
class EvalQuestion:
    """One line of the eval set."""

    id: str
    question: str
    expected_listing_ids: tuple[int, ...]
    source: str  # "seed" (drafted from real listings) or "toni" (#13)
    note: str | None = None


def _listing_ids(value: object) -> tuple[int, ...]:
    """A JSON list of integer ids. Strings, floats and booleans are rejected, not converted
    (a string "63996799" would otherwise become the digits 6, 3, 9, ...)."""
    # bool is a subclass of int in Python (True == 1), so it has to be ruled out by name.
    if not isinstance(value, list) or not all(
        isinstance(i, int) and not isinstance(i, bool) for i in value
    ):
        raise ValueError(f"expected_listing_ids must be a list of integers, got {value!r}")
    return tuple(value)


def load_questions(path: Path) -> list[EvalQuestion]:
    """Read a JSONL eval set: one JSON object per line, blank lines skipped."""
    questions: list[EvalQuestion] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            question = EvalQuestion(
                id=str(row["id"]),
                question=str(row["question"]),
                expected_listing_ids=_listing_ids(row["expected_listing_ids"]),
                source=str(row["source"]),
                note=row.get("note"),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path}: line {number} is not a valid question: {exc}") from exc
        if question.id in seen:
            raise ValueError(f"{path}: line {number} has a duplicate id {question.id!r}")
        seen.add(question.id)
        questions.append(question)
    return questions


def _check_expected(expected: Sequence[int]) -> None:
    if not expected:
        raise ValueError("no expected ids: recall and reciprocal rank are undefined")


def recall_at_k(expected: Sequence[int], ranked: Sequence[int], *, k: int) -> float:
    """Share of `expected` found in the first `k` of `ranked`."""
    _check_expected(expected)
    if k < 1:
        raise ValueError("k must be at least 1")
    found = set(expected) & set(ranked[:k])
    return len(found) / len(set(expected))


def reciprocal_rank(expected: Sequence[int], ranked: Sequence[int]) -> float:
    """1 / (1-based position of the first expected id in `ranked`), or 0."""
    _check_expected(expected)
    wanted = set(expected)
    for position, listing_id in enumerate(ranked, start=1):
        if listing_id in wanted:
            return 1 / position
    return 0.0


@dataclass(frozen=True)
class QuestionScore:
    recall_at_5: float
    recall_at_10: float
    reciprocal_rank: float


@dataclass(frozen=True)
class RunScores:
    """Averages over the `scored` questions, plus each question's own numbers."""

    scored: int
    recall_at_5: float
    recall_at_10: float
    mrr: float
    per_question: dict[str, QuestionScore]
    no_answer_ids: tuple[str, ...]


def score_run(questions: Sequence[EvalQuestion], results: Mapping[str, Sequence[int]]) -> RunScores:
    """Score one search run. `results` maps question id -> ranked listing ids."""
    per_question: dict[str, QuestionScore] = {}
    no_answer: list[str] = []
    for question in questions:
        ranked = results[question.id]  # KeyError: the run skipped a question
        if not question.expected_listing_ids:
            no_answer.append(question.id)
            continue
        expected = question.expected_listing_ids
        per_question[question.id] = QuestionScore(
            recall_at_5=recall_at_k(expected, ranked, k=5),
            recall_at_10=recall_at_k(expected, ranked, k=10),
            reciprocal_rank=reciprocal_rank(expected, ranked),
        )
    if not per_question:
        raise ValueError("no question with expected ids to score")
    count = len(per_question)
    scores = per_question.values()
    return RunScores(
        scored=count,
        recall_at_5=sum(s.recall_at_5 for s in scores) / count,
        recall_at_10=sum(s.recall_at_10 for s in scores) / count,
        mrr=sum(s.reciprocal_rank for s in scores) / count,
        per_question=per_question,
        no_answer_ids=tuple(no_answer),
    )


# A search under test: question text -> ranked listing ids (like a TS
# `(text: string) => Promise<number[]>`).
SearchIds = Callable[[str], Awaitable[list[int]]]


async def run_eval(questions: Sequence[EvalQuestion], search: SearchIds) -> dict[str, list[int]]:
    """Run every question through `search`, one at a time: question id -> ranked ids."""
    return {question.id: await search(question.question) for question in questions}


async def run_keyword_eval(
    conn: psycopg.AsyncConnection, questions: Sequence[EvalQuestion], *, limit: int = 10
) -> dict[str, list[int]]:
    """Run every question through `search_keyword` (no filters): question id -> ranked ids."""

    async def search(text: str) -> list[int]:
        return [hit.listing_id for hit in await search_keyword(conn, text, limit=limit)]

    return await run_eval(questions, search)


async def run_search_eval(
    conn: psycopg.AsyncConnection,
    questions: Sequence[EvalQuestion],
    mode: str,
    *,
    embedder: Embedder | None = None,
    limit: int = 10,
) -> dict[str, list[int]]:
    """Run the set through one search: "keyword", "vector" or "hybrid" (no filters).

    "vector" and "hybrid" need `embedder`, the model whose vectors are in the database.
    """
    if mode == "keyword":
        return await run_keyword_eval(conn, questions, limit=limit)
    if embedder is None:
        raise ValueError(f"{mode} search needs an embedder")
    model = embedder  # a local name the nested functions can close over, typed non-None

    async def vector(text: str) -> list[int]:
        query = await model.embed_query(text)
        return [
            hit.listing_id for hit in await search_vector(conn, query, model.model, limit=limit)
        ]

    async def hybrid(text: str) -> list[int]:
        hits = await search_hybrid(conn, text, embedder=model, limit=limit)
        return [hit.listing_id for hit in hits]

    searches = {"vector": vector, "hybrid": hybrid}
    if mode not in searches:
        raise ValueError(f"unknown search mode {mode!r}")
    return await run_eval(questions, searches[mode])
