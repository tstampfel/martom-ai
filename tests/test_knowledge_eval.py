"""The knowledge eval set (#170): its loader, its scoring and the committed 3D-panel cases."""

import difflib
import json
import re
import unicodedata
from pathlib import Path

import pytest

from martom.evals.knowledge_eval import (
    KnowledgeCase,
    load_knowledge_cases,
    score_knowledge_run,
)
from martom.knowledge import read_chunks

REPO = Path(__file__).resolve().parents[1]
EVAL_SET = REPO / "evals" / "knowledge_3d_panels.jsonl"
QA_FILE = REPO / "knowledge" / "sources" / "martom_3d_panels_qa.jsonl"
QA_ID = re.compile(r"\bp3d-\d{3}\b")

KNOWN = {"3d-panel-boje", "3d-panel-montaza"}


def case(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "k3d-01",
        "question": "koje boje imate",
        "expected_chunk_ids": ["3d-panel-boje"],
        "expected_facts": ["zelena RAL 6005 i antracit RAL 7016"],
        "source_qa_ids": ["p3d-021"],
        "source": "tomislav",
        "approved_by": "",
    }
    row.update(overrides)
    return row


def write_set(tmp_path: Path, *rows: dict[str, object]) -> Path:
    path = tmp_path / "set.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


# --- loader ------------------------------------------------------------------------------


def test_loads_a_valid_case(tmp_path: Path) -> None:
    path = write_set(tmp_path, case())

    assert load_knowledge_cases(path, known_chunk_ids=KNOWN) == [
        KnowledgeCase(
            id="k3d-01",
            question="koje boje imate",
            expected_chunk_ids=("3d-panel-boje",),
            expected_facts=("zelena RAL 6005 i antracit RAL 7016",),
            source_qa_ids=("p3d-021",),
            source="tomislav",
            approved_by="",
        )
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"expected_chunk_ids": ["3d-panel-nema"]}, "not in knowledge/"),
        ({"expected_chunk_ids": []}, "expected_chunk_ids"),
        ({"expected_facts": []}, "expected_facts"),
        ({"expected_facts": ["ok", ""]}, "expected_facts"),
        ({"source_qa_ids": ["021"]}, "source_qa_ids"),
        ({"source": "seed"}, "tomislav"),
        ({"approved_by": None}, "approved_by"),
        ({"question": " "}, "question"),
    ],
)
def test_rejects_an_invalid_case(tmp_path: Path, overrides: dict, message: str) -> None:
    path = write_set(tmp_path, case(**overrides))

    with pytest.raises(ValueError, match=message):
        load_knowledge_cases(path, known_chunk_ids=KNOWN)


def test_rejects_a_missing_field(tmp_path: Path) -> None:
    row = case()
    del row["expected_facts"]

    with pytest.raises(ValueError, match="line 1"):
        load_knowledge_cases(write_set(tmp_path, row), known_chunk_ids=KNOWN)


def test_rejects_a_duplicate_id(tmp_path: Path) -> None:
    path = write_set(tmp_path, case(), case())

    with pytest.raises(ValueError, match="duplicate id"):
        load_knowledge_cases(path, known_chunk_ids=KNOWN)


# --- scoring -----------------------------------------------------------------------------


def test_scores_recall_and_reciprocal_rank() -> None:
    cases = [
        KnowledgeCase("a", "q", ("x",), ("f",), ("p3d-001",), "tomislav", ""),
        KnowledgeCase("b", "q", ("y", "z"), ("f",), ("p3d-002",), "tomislav", ""),
    ]
    results = {
        "a": ["x", "o"],  # first: R@5 1, RR 1
        "b": ["o", "o", "o", "o", "o", "y"],  # y 6th: R@5 0, R@10 0.5, RR 1/6
    }

    scores = score_knowledge_run(cases, results)

    assert scores.recall_at_5 == pytest.approx(0.5)
    assert scores.recall_at_10 == pytest.approx(0.75)
    assert scores.mrr == pytest.approx((1 + 1 / 6) / 2)
    assert scores.per_case["b"].recall_at_10 == pytest.approx(0.5)


def test_scoring_needs_a_result_for_every_case() -> None:
    cases = [KnowledgeCase("a", "q", ("x",), ("f",), ("p3d-001",), "tomislav", "")]

    with pytest.raises(ValueError, match="no results for"):
        score_knowledge_run(cases, {})


# --- the committed 3D-panel set ----------------------------------------------------------


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def qa_rows() -> dict[str, dict]:
    lines = QA_FILE.read_text(encoding="utf-8").splitlines()
    return {row["id"]: row for row in map(json.loads, filter(str.strip, lines))}


def repo_cases() -> list[KnowledgeCase]:
    chunk_ids = {chunk.id for chunk in read_chunks(REPO / "knowledge")}
    return load_knowledge_cases(EVAL_SET, known_chunk_ids=chunk_ids)


def test_set_has_about_30_cases() -> None:
    assert 28 <= len(repo_cases()) <= 40


def test_cases_use_only_cleared_entries() -> None:
    rows = qa_rows()

    for item in repo_cases():
        for qa_id in item.source_qa_ids:
            assert qa_id in rows, (item.id, qa_id)
            assert not rows[qa_id].get("needs_check"), (item.id, qa_id)


def test_expected_chunks_hold_the_source_entries() -> None:
    # The chunk that holds a case's source answer must be one the case expects.
    holder = {
        qa_id: chunk.id
        for chunk in read_chunks(REPO / "knowledge")
        for source in chunk.sources
        for qa_id in QA_ID.findall(source)
    }

    for item in repo_cases():
        for qa_id in item.source_qa_ids:
            assert holder[qa_id] in item.expected_chunk_ids, (item.id, qa_id)


def test_questions_are_held_out_from_the_knowledge_base() -> None:
    bodies = [normalise(chunk.body) for chunk in read_chunks(REPO / "knowledge")]
    toni_questions = [normalise(row["question"]) for row in qa_rows().values()]

    for item in repo_cases():
        question = normalise(item.question)
        assert not any(question in body for body in bodies), item.id
        # Paraphrased, never Toni's question with a word changed.
        closest = max(
            difflib.SequenceMatcher(None, question, toni).ratio() for toni in toni_questions
        )
        assert closest < 0.75, (item.id, closest)


def test_cases_wait_for_approval() -> None:
    # Only Tomislav (or Toni) approves a case, in the PR review: an agent never fills
    # approved_by. Tomislav changes this test in the commit that records his approvals.
    assert [item.id for item in repo_cases() if item.approved_by] == []
