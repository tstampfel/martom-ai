"""Retrieval metrics (recall@k, MRR) and the eval set loader."""

import json
from pathlib import Path

import pytest

from martom.evals.retrieval import (
    EvalQuestion,
    load_questions,
    recall_at_k,
    reciprocal_rank,
    score_run,
)

REPO_EVAL_SET = Path(__file__).parents[1] / "evals" / "retrieval.jsonl"


# --- recall@k --------------------------------------------------------------------------


def test_recall_counts_expected_ids_found_in_the_top_k() -> None:
    # 2 of the 3 expected ids are in the top 3.
    assert recall_at_k([1, 2, 3], [9, 1, 2, 3], k=3) == pytest.approx(2 / 3)


def test_recall_ignores_hits_below_k() -> None:
    assert recall_at_k([5], [1, 2, 3, 4, 5], k=4) == 0.0
    assert recall_at_k([5], [1, 2, 3, 4, 5], k=5) == 1.0


def test_recall_counts_a_repeated_result_once() -> None:
    assert recall_at_k([1, 2], [1, 1, 1], k=3) == 0.5


def test_recall_is_undefined_without_expected_ids() -> None:
    with pytest.raises(ValueError, match="no expected ids"):
        recall_at_k([], [1, 2], k=10)


def test_recall_needs_a_positive_k() -> None:
    with pytest.raises(ValueError, match="k must be"):
        recall_at_k([1], [1], k=0)


# --- MRR -------------------------------------------------------------------------------


def test_reciprocal_rank_is_one_over_the_first_relevant_position() -> None:
    assert reciprocal_rank([7, 8], [1, 2, 8, 7]) == pytest.approx(1 / 3)
    assert reciprocal_rank([1], [1, 2]) == 1.0


def test_reciprocal_rank_is_zero_when_nothing_relevant_is_returned() -> None:
    assert reciprocal_rank([7], [1, 2, 3]) == 0.0


def test_reciprocal_rank_is_undefined_without_expected_ids() -> None:
    with pytest.raises(ValueError, match="no expected ids"):
        reciprocal_rank([], [1])


# --- whole run -------------------------------------------------------------------------


def _q(qid: str, expected: list[int]) -> EvalQuestion:
    return EvalQuestion(id=qid, question=qid, expected_listing_ids=tuple(expected), source="seed")


def test_score_run_averages_over_questions_with_expected_ids() -> None:
    questions = [_q("a", [1]), _q("b", [2, 3]), _q("off", [])]
    results = {
        "a": [1, 9],  # recall 1, RR 1
        "b": [9, 9, 9, 9, 9, 3],  # recall@5 0, recall@10 0.5, RR 1/6
        "off": [4, 5],  # off-topic: not in the averages
    }
    scores = score_run(questions, results)

    assert scores.scored == 2
    assert scores.recall_at_5 == pytest.approx((1 + 0) / 2)
    assert scores.recall_at_10 == pytest.approx((1 + 0.5) / 2)
    assert scores.mrr == pytest.approx((1 + 1 / 6) / 2)
    assert scores.no_answer_ids == ("off",)
    assert scores.per_question["b"].recall_at_10 == 0.5


def test_score_run_needs_a_result_for_every_question() -> None:
    with pytest.raises(KeyError, match="b"):
        score_run([_q("a", [1]), _q("b", [2])], {"a": [1]})


def test_score_run_without_any_scorable_question_fails() -> None:
    with pytest.raises(ValueError, match="no question with expected ids"):
        score_run([_q("off", [])], {"off": []})


# --- loader ----------------------------------------------------------------------------


def test_load_questions_reads_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    rows = [
        {
            "id": "q1",
            "question": "Panel 153",
            "expected_listing_ids": [63996799],
            "source": "seed",
            "note": "panel",
        },
        {"id": "q2", "question": "Koliko je sati?", "expected_listing_ids": [], "source": "seed"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", encoding="utf-8")

    questions = load_questions(path)

    assert questions == [
        EvalQuestion("q1", "Panel 153", (63996799,), "seed", "panel"),
        EvalQuestion("q2", "Koliko je sati?", (), "seed", None),
    ]


def test_load_questions_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    row = json.dumps({"id": "q1", "question": "x", "expected_listing_ids": [], "source": "seed"})
    path.write_text(f"{row}\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate id 'q1'"):
        load_questions(path)


def test_load_questions_names_the_bad_line(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    path.write_text('{"id": "q1", "question": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_questions(path)


def test_the_repo_eval_set_loads_and_has_seed_questions() -> None:
    questions = load_questions(REPO_EVAL_SET)
    assert len(questions) >= 10
    assert all(q.source in {"seed", "toni"} for q in questions)
    # At least one off-topic question that expects no listing.
    assert any(not q.expected_listing_ids for q in questions)
