"""Reciprocal rank fusion (M3-3): merge ranked lists by summing 1 / (k + rank)."""

import pytest

from martom.search.hybrid import RRF_K, reciprocal_rank_fusion


def test_an_id_in_both_lists_beats_ids_in_one() -> None:
    fused = reciprocal_rank_fusion([[1, 2, 3], [4, 2, 5]])
    assert fused[0][0] == 2
    assert fused[0][1] == pytest.approx(2 / (RRF_K + 2))


def test_scores_are_summed_per_list_with_one_based_ranks() -> None:
    fused = dict(reciprocal_rank_fusion([[7, 8], [8]], k=10))
    assert fused[7] == pytest.approx(1 / 11)
    assert fused[8] == pytest.approx(1 / 12 + 1 / 11)


def test_ties_keep_the_better_single_rank_then_the_smaller_id() -> None:
    # 10 and 20 are both first in one list: equal scores, so the smaller id goes first.
    # 30 (second in one list) ranks after them.
    assert [i for i, _ in reciprocal_rank_fusion([[20, 30], [10]])] == [10, 20, 30]


def test_empty_lists_fuse_to_nothing() -> None:
    assert reciprocal_rank_fusion([[], []]) == []


def test_a_repeated_id_in_one_list_counts_once_at_its_best_rank() -> None:
    fused = dict(reciprocal_rank_fusion([[5, 5, 6]], k=0))
    assert fused[5] == pytest.approx(1.0)
    assert fused[6] == pytest.approx(1 / 3)
