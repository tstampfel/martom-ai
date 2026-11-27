"""Retrieval metrics (recall@k, MRR) and the eval set loader."""

import json
from pathlib import Path

import pytest

from martom.evals.retrieval import (
    ISSUE_102_CASES,
    EvalQuestion,
    case_status,
    forbidden_in_top_k,
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


# --- must not include ------------------------------------------------------------------


def test_forbidden_in_top_k_finds_a_forbidden_id_in_the_top_k() -> None:
    assert forbidden_in_top_k([52889522], [1, 52889522, 3], k=10) is True


def test_forbidden_in_top_k_ignores_a_forbidden_id_below_k() -> None:
    assert forbidden_in_top_k([5], [1, 2, 3, 4, 5], k=4) is False
    assert forbidden_in_top_k([5], [1, 2, 3, 4, 5], k=5) is True


def test_forbidden_in_top_k_needs_forbidden_ids_and_a_positive_k() -> None:
    with pytest.raises(ValueError, match="no forbidden ids"):
        forbidden_in_top_k([], [1], k=10)
    with pytest.raises(ValueError, match="k must be"):
        forbidden_in_top_k([1], [1], k=0)


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


def test_score_run_reports_questions_whose_top_10_has_a_forbidden_id() -> None:
    questions = [
        EvalQuestion("kolica", "kolica", (1,), "seed", must_not_include_listing_ids=(99,)),
        EvalQuestion("clean", "x", (2,), "seed", must_not_include_listing_ids=(98,)),
        EvalQuestion("off", "y", (), "seed", must_not_include_listing_ids=(97,)),
        _q("plain", [3]),
    ]
    results = {"kolica": [1, 99], "clean": [2], "off": [97], "plain": [3]}

    scores = score_run(questions, results)

    # Off-topic questions are checked too: "must not appear" needs no expected ids.
    assert scores.forbidden_checked == 3
    assert scores.forbidden_hit_ids == ("kolica", "off")
    assert scores.forbidden_hit_rate == pytest.approx(2 / 3)
    # The forbidden check does not change recall or MRR.
    assert scores.per_question["kolica"].recall_at_5 == 1.0
    assert scores.mrr == 1.0


def test_score_run_without_forbidden_ids_has_no_forbidden_rate() -> None:
    scores = score_run([_q("a", [1])], {"a": [1]})
    assert scores.forbidden_checked == 0
    assert scores.forbidden_hit_rate is None
    assert scores.forbidden_hit_ids == ()


def test_case_status_says_ok_or_names_each_problem() -> None:
    questions = [
        EvalQuestion("good", "kolica", (1, 2), "seed", must_not_include_listing_ids=(9,)),
        EvalQuestion("partial", "panelan", (1, 2), "seed"),
        EvalQuestion("forbidden", "kolica", (1,), "seed", must_not_include_listing_ids=(9,)),
        EvalQuestion("both", "kolica", (1, 2), "seed", must_not_include_listing_ids=(9,)),
        EvalQuestion("off-topic", "sati", (), "seed", must_not_include_listing_ids=(9,)),
    ]
    scores = score_run(
        questions,
        {"good": [2, 1], "partial": [1], "forbidden": [9, 1], "both": [9, 2], "off-topic": [3]},
    )
    assert case_status(scores, "good") == "ok"
    assert case_status(scores, "partial") == "R@10 0.50"
    assert case_status(scores, "forbidden") == "FORBIDDEN"
    assert case_status(scores, "both") == "FORBIDDEN, R@10 0.50"
    assert case_status(scores, "off-topic") == "ok"
    # An eval set without the case (compare_embeddings.py --set other.jsonl).
    assert case_status(scores, "seed-12-kolica") == "-"


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
    # Lines without the optional field forbid nothing.
    assert all(q.must_not_include_listing_ids == () for q in questions)


def test_load_questions_reads_must_not_include_listing_ids(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    row = {
        "id": "q1",
        "question": "kolica",
        "expected_listing_ids": [1],
        "must_not_include_listing_ids": [52889522],
        "source": "seed",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    [question] = load_questions(path)

    assert question.must_not_include_listing_ids == (52889522,)


def test_load_questions_rejects_an_id_both_expected_and_forbidden(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    row = {
        "id": "q1",
        "question": "x",
        "expected_listing_ids": [1, 2],
        "must_not_include_listing_ids": [2],
        "source": "seed",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_questions(path)


@pytest.mark.parametrize("bad", ['"52889522"', "[1.5]", "[true]", "[]"])
def test_load_questions_needs_a_non_empty_list_of_integer_forbidden_ids(
    tmp_path: Path, bad: str
) -> None:
    path = tmp_path / "set.jsonl"
    line = (
        '{"id": "q1", "question": "x", "expected_listing_ids": [1],'
        f' "must_not_include_listing_ids": {bad}, "source": "seed"}}'
    )
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_questions(path)


def test_load_questions_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    row = json.dumps({"id": "q1", "question": "x", "expected_listing_ids": [], "source": "seed"})
    path.write_text(f"{row}\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate id 'q1'"):
        load_questions(path)


@pytest.mark.parametrize("bad", ['"63996799"', "[1.5]", "[true]", "63996799", '["1"]'])
def test_load_questions_needs_a_list_of_integer_ids(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "set.jsonl"
    line = f'{{"id": "q1", "question": "x", "expected_listing_ids": {bad}, "source": "seed"}}'
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
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


def test_run_eval_asks_every_question_and_keeps_the_ranked_ids() -> None:
    import asyncio

    from martom.evals.retrieval import EvalQuestion, run_eval

    questions = [
        EvalQuestion("a", "panel", (1,), "seed"),
        EvalQuestion("b", "stup", (2,), "seed"),
    ]
    asked: list[str] = []

    async def search(text: str) -> list[int]:
        asked.append(text)
        return [len(text), 9]

    assert asyncio.run(run_eval(questions, search)) == {"a": [5, 9], "b": [4, 9]}
    assert asked == ["panel", "stup"]


def test_the_repo_eval_set_pins_the_kolica_and_panelan_cases() -> None:
    # #102: "kolica" must not return the angle grinder (a trigram false positive), and the
    # bare typo "panelan" must still find both panel listings.
    questions = {q.question: q for q in load_questions(REPO_EVAL_SET)}
    assert 52889522 in questions["kolica"].must_not_include_listing_ids
    assert set(questions["panelan"].expected_listing_ids) == {63996799, 1199211}


def test_the_issue_102_cases_are_in_the_repo_eval_set() -> None:
    # A renamed case would otherwise show "-" in compare_embeddings.py and eval_retrieve.py.
    ids = {q.id for q in load_questions(REPO_EVAL_SET)}
    assert set(ISSUE_102_CASES) <= ids


# --- manual products (#112) ------------------------------------------------------------


def test_load_questions_reads_expected_manual_keys(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    row = {
        "id": "q1",
        "question": "Anker M8x70 za stup sa stopom",
        "expected_listing_ids": [],
        "expected_manual_keys": ["anchor_m8x70"],
        "source": "seed",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    (question,) = load_questions(path)

    assert question.expected_manual_keys == ("anchor_m8x70",)
    assert question.expected_ids == ("anchor_m8x70",)


@pytest.mark.parametrize("bad", ["[]", '[""]', "[1]", '"anchor_m8x70"'])
def test_load_questions_needs_a_non_empty_list_of_manual_keys(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "set.jsonl"
    path.write_text(
        '{"id": "q1", "question": "x", "expected_listing_ids": [],'
        f' "expected_manual_keys": {bad}, "source": "seed"}}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected_manual_keys"):
        load_questions(path)


def test_score_run_counts_a_manual_key_in_the_ranked_results() -> None:
    questions = [EvalQuestion("q1", "anker", (40021189,), "seed", expected_manual_keys=("a",))]
    # A manual product shows up in the ranked results as its key (a string); a listing as
    # its id (an int), so the two can never be confused.
    scores = score_run(questions, {"q1": ["a", 1, 2, 3, 4, 40021189]})
    assert scores.per_question["q1"].recall_at_5 == 0.5
    assert scores.per_question["q1"].recall_at_10 == 1.0
    assert scores.mrr == 1.0


def test_eval_id_is_the_listing_id_or_the_manual_key() -> None:
    from martom.evals.retrieval import eval_id
    from martom.search.hybrid import HybridHit

    olx = HybridHit(63996799, "PANEL", "panel-63996799", "panel", 0.1, 1, 1)
    manual = HybridHit(
        None, "Anker", None, "Anker", 0.1, 1, 1, source="manual", manual_key="anchor_m8x70"
    )
    assert eval_id(olx) == 63996799
    assert eval_id(manual) == "anchor_m8x70"


def test_the_repo_eval_set_asks_for_the_manual_m8x70_anchor() -> None:
    from martom.bom.manual_products import load_manual_products

    questions = load_questions(REPO_EVAL_SET)
    manual = [q for q in questions if q.expected_manual_keys]
    assert any("anchor_m8x70" in q.expected_manual_keys for q in manual)
    # Every manual key in the set is a real manual_products.toml entry.
    known = set(load_manual_products())
    assert all(set(q.expected_manual_keys) <= known for q in manual)
