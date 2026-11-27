"""Retrieval metrics for the eval set in `evals/retrieval.jsonl` (docs/PLAN.md, "Evals").

- recall@k: the share of a question's expected listings that appear in the top k results.
  "Did search find what Toni would sell?"
- reciprocal rank: 1 / position of the first expected listing (0 if none is returned).
  MRR is its mean over questions. "How high is the first right answer?"

- forbidden hit rate: the share of questions with `must_not_include_listing_ids` that get one
  of those ids in their top 10. "Did search return something it must not?" (#102: "kolica"
  must not return the angle grinder.) Optional per question; checked on off-topic questions too.

Manual products (#112) have no listing id: a question expects them by their
manual_products.toml key in `expected_manual_keys`, and a search's ranked results hold a
manual hit as that key (a str) and a listing as its id (an int), via `eval_id`. Ints and
strings never compare equal, so the two can't be confused.

Questions with no expected ids (off-topic ones such as "Koliko je sati?") have no recall or
reciprocal rank, so they are left out of the averages and listed in `no_answer_ids`; a
later issue (the guardrail, M4) decides what search should return for them.
"""

import json
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psycopg

from martom.bom.manual_products import load_manual_products
from martom.embeddings import Embedder
from martom.search.hybrid import search_hybrid
from martom.search.keyword import search_keyword
from martom.search.vector import search_vector

# One ranked result: an OLX listing id (int) or a manual product's TOML key (str).
EvalId = int | str


class _HasSource(Protocol):
    """Any search hit (KeywordHit, VectorHit, HybridHit, RetrievedHit)."""

    @property
    def listing_id(self) -> int | None: ...
    @property
    def source(self) -> str: ...
    @property
    def manual_key(self) -> str | None: ...


def eval_id(hit: _HasSource) -> EvalId:
    """How a hit appears in a ranked eval result: the manual key, or the listing id."""
    if hit.source == "manual":
        assert hit.manual_key is not None
        return hit.manual_key
    assert hit.listing_id is not None
    return hit.listing_id


@dataclass(frozen=True)
class EvalQuestion:
    """One line of the eval set."""

    id: str
    question: str
    expected_listing_ids: tuple[int, ...]
    source: str  # "seed" (drafted from real listings) or "toni" (#13)
    note: str | None = None
    # Ids that must not appear in the top 10 (optional; empty = nothing forbidden).
    must_not_include_listing_ids: tuple[int, ...] = ()
    # manual_products.toml keys the question expects (optional), e.g. ("anchor_m8x70",).
    expected_manual_keys: tuple[str, ...] = ()

    @property
    def expected_ids(self) -> tuple[EvalId, ...]:
        """Every expected result: listing ids and manual keys."""
        return self.expected_listing_ids + self.expected_manual_keys


def _listing_ids(value: object, field: str = "expected_listing_ids") -> tuple[int, ...]:
    """A JSON list of integer ids. Strings, floats and booleans are rejected, not converted
    (a string "63996799" would otherwise become the digits 6, 3, 9, ...)."""
    # bool is a subclass of int in Python (True == 1), so it has to be ruled out by name.
    if not isinstance(value, list) or not all(
        isinstance(i, int) and not isinstance(i, bool) for i in value
    ):
        raise ValueError(f"{field} must be a list of integers, got {value!r}")
    return tuple(value)


def _forbidden_ids(row: dict, expected: tuple[int, ...]) -> tuple[int, ...]:
    """The optional `must_not_include_listing_ids`: absent = (), present = a non-empty list of
    integer ids, none of them also expected."""
    if "must_not_include_listing_ids" not in row:
        return ()
    forbidden = _listing_ids(row["must_not_include_listing_ids"], "must_not_include_listing_ids")
    if not forbidden:
        raise ValueError("must_not_include_listing_ids is empty: leave the field out instead")
    if both := set(forbidden) & set(expected):
        raise ValueError(f"ids both expected and forbidden: {sorted(both)}")
    return forbidden


def _manual_keys(row: dict, known: Collection[str]) -> tuple[str, ...]:
    """The optional `expected_manual_keys`: absent = (), present = a non-empty list of
    keys that are in `known` (a misspelled key could never match, R1-7)."""
    if "expected_manual_keys" not in row:
        return ()
    value = row["expected_manual_keys"]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(key, str) and key for key in value)
    ):
        raise ValueError(
            f"expected_manual_keys must be a non-empty list of manual product keys, got {value!r}"
        )
    if unknown := sorted(set(value) - set(known)):
        raise ValueError(f"expected_manual_keys not in manual_products.toml: {unknown}")
    return tuple(value)


def load_questions(path: Path, *, manual_keys: Collection[str] | None = None) -> list[EvalQuestion]:
    """Read a JSONL eval set: one JSON object per line, blank lines skipped.

    `manual_keys` are the manual product keys a question may expect; by default the keys
    in manual_products.toml.
    """
    known = set(load_manual_products()) if manual_keys is None else set(manual_keys)
    questions: list[EvalQuestion] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            expected = _listing_ids(row["expected_listing_ids"])
            question = EvalQuestion(
                id=str(row["id"]),
                question=str(row["question"]),
                expected_listing_ids=expected,
                source=str(row["source"]),
                note=row.get("note"),
                must_not_include_listing_ids=_forbidden_ids(row, expected),
                expected_manual_keys=_manual_keys(row, known),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path}: line {number} is not a valid question: {exc}") from exc
        if question.id in seen:
            raise ValueError(f"{path}: line {number} has a duplicate id {question.id!r}")
        seen.add(question.id)
        questions.append(question)
    return questions


def _check_expected(expected: Sequence[EvalId]) -> None:
    if not expected:
        raise ValueError("no expected ids: recall and reciprocal rank are undefined")


def recall_at_k(expected: Sequence[EvalId], ranked: Sequence[EvalId], *, k: int) -> float:
    """Share of `expected` found in the first `k` of `ranked`."""
    _check_expected(expected)
    if k < 1:
        raise ValueError("k must be at least 1")
    found = set(expected) & set(ranked[:k])
    return len(found) / len(set(expected))


def reciprocal_rank(expected: Sequence[EvalId], ranked: Sequence[EvalId]) -> float:
    """1 / (1-based position of the first expected id in `ranked`), or 0."""
    _check_expected(expected)
    wanted = set(expected)
    for position, result in enumerate(ranked, start=1):
        if result in wanted:
            return 1 / position
    return 0.0


def forbidden_in_top_k(forbidden: Sequence[EvalId], ranked: Sequence[EvalId], *, k: int) -> bool:
    """True when any `forbidden` id is in the first `k` of `ranked`."""
    if not forbidden:
        raise ValueError("no forbidden ids: nothing to check")
    if k < 1:
        raise ValueError("k must be at least 1")
    return bool(set(forbidden) & set(ranked[:k]))


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
    # Questions with must_not_include_listing_ids, and those whose top 10 had one.
    forbidden_checked: int = 0
    forbidden_hit_ids: tuple[str, ...] = ()

    @property
    def forbidden_hit_rate(self) -> float | None:
        """Share of the checked questions with a forbidden id in the top 10 (lower is better);
        None when no question forbids anything."""
        if not self.forbidden_checked:
            return None
        return len(self.forbidden_hit_ids) / self.forbidden_checked


def score_run(
    questions: Sequence[EvalQuestion], results: Mapping[str, Sequence[EvalId]]
) -> RunScores:
    """Score one search run. `results` maps question id -> ranked ids (see EvalId)."""
    per_question: dict[str, QuestionScore] = {}
    no_answer: list[str] = []
    forbidden_checked = 0
    forbidden_hits: list[str] = []
    for question in questions:
        ranked = results[question.id]  # KeyError: the run skipped a question
        if question.must_not_include_listing_ids:
            forbidden_checked += 1
            if forbidden_in_top_k(question.must_not_include_listing_ids, ranked, k=10):
                forbidden_hits.append(question.id)
        if not question.expected_ids:
            no_answer.append(question.id)
            continue
        expected = question.expected_ids
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
        forbidden_checked=forbidden_checked,
        forbidden_hit_ids=tuple(forbidden_hits),
    )


# The #102 eval cases the comparison scripts show per run: "kolica" and "građevinska kolica"
# must not return the grinder, the bare typo "panelan" must find the panels.
ISSUE_102_CASES = ("seed-12-kolica", "seed-13-typo-panelan", "seed-14-gradjevinska-kolica")


def case_status(scores: RunScores, question_id: str) -> str:
    """One question's result in a few characters, for a comparison table: "ok" (full recall@10
    and no forbidden id in the top 10), else "FORBIDDEN" and/or "R@10 0.50"; "-" when the run
    has no such question (another eval set)."""
    score = scores.per_question.get(question_id)
    if score is None and question_id not in scores.no_answer_ids:
        return "-"
    problems: list[str] = []
    if question_id in scores.forbidden_hit_ids:
        problems.append("FORBIDDEN")
    if score is not None and score.recall_at_10 < 1:
        problems.append(f"R@10 {score.recall_at_10:.2f}")
    return ", ".join(problems) or "ok"


async def missing_manual_keys(
    conn: psycopg.AsyncConnection, questions: Sequence[EvalQuestion]
) -> list[str]:
    """The expected manual keys that have no `manual_product` row in this database.

    The eval scripts stop on any: without the row (`martom load-manual-products` or
    `martom embed` never ran) the question would score 0 and look like a search regression.
    """
    wanted = sorted({key for q in questions for key in q.expected_manual_keys})
    if not wanted:
        return []
    cursor = await conn.execute("SELECT key FROM manual_product WHERE key = ANY(%s)", (wanted,))
    present = {row[0] for row in await cursor.fetchall()}
    return [key for key in wanted if key not in present]


# A search under test: question text -> ranked ids (like a TS
# `(text: string) => Promise<(number | string)[]>`).
SearchIds = Callable[[str], Awaitable[list[EvalId]]]


async def run_eval(questions: Sequence[EvalQuestion], search: SearchIds) -> dict[str, list[EvalId]]:
    """Run every question through `search`, one at a time: question id -> ranked ids."""
    return {question.id: await search(question.question) for question in questions}


async def run_keyword_eval(
    conn: psycopg.AsyncConnection, questions: Sequence[EvalQuestion], *, limit: int = 10
) -> dict[str, list[EvalId]]:
    """Run every question through `search_keyword` (no filters): question id -> ranked ids."""

    async def search(text: str) -> list[EvalId]:
        return [eval_id(hit) for hit in await search_keyword(conn, text, limit=limit)]

    return await run_eval(questions, search)


async def run_search_eval(
    conn: psycopg.AsyncConnection,
    questions: Sequence[EvalQuestion],
    mode: str,
    *,
    embedder: Embedder | None = None,
    limit: int = 10,
) -> dict[str, list[EvalId]]:
    """Run the set through one search: "keyword", "vector" or "hybrid" (no filters).

    "vector" and "hybrid" need `embedder`, the model whose vectors are in the database.
    """
    if mode == "keyword":
        return await run_keyword_eval(conn, questions, limit=limit)
    if embedder is None:
        raise ValueError(f"{mode} search needs an embedder")
    model = embedder  # a local name the nested functions can close over, typed non-None

    async def vector(text: str) -> list[EvalId]:
        query = await model.embed_query(text)
        return [eval_id(hit) for hit in await search_vector(conn, query, model.model, limit=limit)]

    async def hybrid(text: str) -> list[EvalId]:
        # fallback=False: a failed query embedding stops the eval instead of quietly
        # turning this question into a keyword-only answer scored as hybrid.
        hits = await search_hybrid(conn, text, embedder=model, limit=limit, fallback=False)
        return [eval_id(hit) for hit in hits]

    searches = {"vector": vector, "hybrid": hybrid}
    if mode not in searches:
        raise ValueError(f"unknown search mode {mode!r}")
    return await run_eval(questions, searches[mode])
