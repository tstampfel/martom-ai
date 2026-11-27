"""Helpers of the M3-4 retrieval eval (src/martom/evals/retrieve_eval.py). No database."""

import pytest

from martom.evals.retrieve_eval import percentile


def test_percentile_uses_the_nearest_rank() -> None:
    samples = [float(n) for n in range(1, 21)]  # 1..20
    assert percentile(samples, 50) == 10.0
    assert percentile(samples, 95) == 19.0  # ceil(0.95 * 20) = 19th smallest
    assert percentile(samples, 100) == 20.0


def test_percentile_of_a_few_samples_is_their_maximum_near_the_top() -> None:
    assert percentile([300.0, 100.0, 200.0], 95) == 300.0


def test_percentile_needs_samples() -> None:
    with pytest.raises(ValueError):
        percentile([], 95)
