"""Knowledge retrieval eval (#170): does knowledge search find the chunk that answers a question?

The cases are hand-written, paraphrased customer questions (`evals/knowledge_3d_panels.jsonl`).
Each one names the knowledge chunk ids that hold the answer (`expected_chunk_ids`), the facts a
good answer states (`expected_facts`, for the later answer-level eval in M4-5) and the entries
of Toni's Q&A the facts come from (`source_qa_ids`). The questions are never Toni's own
wording: the knowledge base holds his answers, so his question would find its own answer too
easily.

Scoring reuses the retrieval metrics (recall@5, recall@10, reciprocal rank) from
`martom.evals.retrieval`, over chunk ids instead of listing ids.
"""

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

from martom.embeddings import Embedder
from martom.evals.retrieval import recall_at_k, reciprocal_rank
from martom.search.vector import search_knowledge

QA_ID = re.compile(r"^p3d-\d{3}$")


@dataclass(frozen=True)
class KnowledgeCase:
    """One line of a knowledge eval set."""

    id: str
    question: str
    expected_chunk_ids: tuple[str, ...]
    expected_facts: tuple[str, ...]
    source_qa_ids: tuple[str, ...]
    source: str  # always "tomislav": hand-written (the evals/ policy, #158)
    approved_by: str  # "" until Tomislav (or Toni) approves the case in review


def _strings(row: dict, field: str) -> tuple[str, ...]:
    """A non-empty JSON list of non-empty strings."""
    value = row[field]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ValueError(f"{field} must be a non-empty list of non-empty strings, got {value!r}")
    return tuple(value)


def _parse_case(row: dict, known_chunk_ids: Collection[str]) -> KnowledgeCase:
    question = row["question"]
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")

    chunk_ids = _strings(row, "expected_chunk_ids")
    if unknown := sorted(set(chunk_ids) - set(known_chunk_ids)):
        raise ValueError(f"expected_chunk_ids not in knowledge/: {unknown}")

    qa_ids = _strings(row, "source_qa_ids")
    if bad := [qa_id for qa_id in qa_ids if not QA_ID.match(qa_id)]:
        raise ValueError(f"source_qa_ids must look like 'p3d-042', got {bad}")

    if row["source"] != "tomislav":
        raise ValueError(f"source must be 'tomislav' (hand-written only), got {row['source']!r}")

    approved_by = row["approved_by"]
    if not isinstance(approved_by, str):
        raise ValueError(f"approved_by must be a string ('' until approved), got {approved_by!r}")

    return KnowledgeCase(
        id=str(row["id"]),
        question=question,
        expected_chunk_ids=chunk_ids,
        expected_facts=_strings(row, "expected_facts"),
        source_qa_ids=qa_ids,
        source="tomislav",
        approved_by=approved_by,
    )


def load_knowledge_cases(path: Path, *, known_chunk_ids: Collection[str]) -> list[KnowledgeCase]:
    """Read a JSONL knowledge eval set: one JSON object per line, blank lines skipped.

    `known_chunk_ids` are the chunk ids in `knowledge/` (`read_chunks`), so a misspelled or
    deleted chunk id fails here instead of scoring 0.
    """
    cases: list[KnowledgeCase] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = _parse_case(json.loads(line), known_chunk_ids)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path}: line {number} is not a valid case: {exc}") from exc
        if item.id in seen:
            raise ValueError(f"{path}: line {number} has a duplicate id {item.id!r}")
        seen.add(item.id)
        cases.append(item)
    return cases


@dataclass(frozen=True)
class CaseScore:
    recall_at_5: float
    recall_at_10: float
    reciprocal_rank: float


@dataclass(frozen=True)
class KnowledgeScores:
    """Averages over every case, plus each case's own numbers."""

    recall_at_5: float
    recall_at_10: float
    mrr: float
    per_case: dict[str, CaseScore]


def score_knowledge_run(
    cases: Sequence[KnowledgeCase], results: Mapping[str, Sequence[str]]
) -> KnowledgeScores:
    """Score ranked chunk ids per case id. Every case needs a result (an empty list is fine)."""
    if missing := [item.id for item in cases if item.id not in results]:
        raise ValueError(f"no results for {missing}")
    per_case = {
        item.id: CaseScore(
            recall_at_5=recall_at_k(item.expected_chunk_ids, results[item.id], k=5),
            recall_at_10=recall_at_k(item.expected_chunk_ids, results[item.id], k=10),
            reciprocal_rank=reciprocal_rank(item.expected_chunk_ids, results[item.id]),
        )
        for item in cases
    }
    count = len(per_case) or 1  # an empty set scores 0, not a ZeroDivisionError
    return KnowledgeScores(
        recall_at_5=sum(s.recall_at_5 for s in per_case.values()) / count,
        recall_at_10=sum(s.recall_at_10 for s in per_case.values()) / count,
        mrr=sum(s.reciprocal_rank for s in per_case.values()) / count,
        per_case=per_case,
    )


async def run_knowledge_eval(
    conn: psycopg.AsyncConnection,
    cases: Sequence[KnowledgeCase],
    *,
    embedder: Embedder,
    limit: int = 10,
) -> dict[str, list[str]]:
    """Run knowledge search for every case: case id -> ranked chunk ids."""
    results: dict[str, list[str]] = {}
    for item in cases:
        hits = await search_knowledge(conn, item.question, embedder=embedder, limit=limit)
        results[item.id] = [hit.id for hit in hits]
    return results
