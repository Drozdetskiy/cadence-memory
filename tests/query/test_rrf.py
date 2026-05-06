"""Unit tests for cadence_memory.query.rrf."""

from __future__ import annotations

import math

from cadence_memory.query.rrf import reciprocal_rank_fusion

K = 60


def _score(*ranks: int, k: int = K) -> float:
    return sum(1.0 / (k + r) for r in ranks)


def test_three_rankings_overlapping_and_disjoint() -> None:
    rankings = [
        ["a", "b", "c", "d", "e"],
        ["b", "a", "f", "c", "g"],
        ["a", "c", "b", "h", "i"],
    ]
    result = reciprocal_rank_fusion(rankings)

    expected_scores = {
        "a": _score(1, 2, 1),
        "b": _score(2, 1, 3),
        "c": _score(3, 4, 2),
        "d": _score(4),
        "e": _score(5),
        "f": _score(3),
        "g": _score(5),
        "h": _score(4),
        "i": _score(5),
    }

    got = dict(result)
    assert got.keys() == expected_scores.keys()
    for item, expected in expected_scores.items():
        assert math.isclose(got[item], expected, rel_tol=1e-12)

    ordered = [item for item, _ in result]
    expected_order = sorted(
        expected_scores,
        key=lambda i: (-expected_scores[i], ["a", "b", "c", "d", "e", "f", "g", "h", "i"].index(i)),
    )
    assert ordered == expected_order


def test_single_ranking_preserves_order() -> None:
    result = reciprocal_rank_fusion([["x", "y", "z"]])
    assert [item for item, _ in result] == ["x", "y", "z"]
    for rank, (_, score) in enumerate(result, start=1):
        assert math.isclose(score, 1.0 / (K + rank), rel_tol=1e-12)


def test_empty_rankings_returns_empty_list() -> None:
    assert reciprocal_rank_fusion([]) == []


def test_inner_empty_rankings_are_skipped() -> None:
    result = reciprocal_rank_fusion([[], ["a", "b"], []])
    assert [item for item, _ in result] == ["a", "b"]


def test_duplicates_use_first_occurrence_rank() -> None:
    """A duplicate within a ranking does not contribute additional score;
    only the first occurrence's rank is counted for that ranking."""
    result = reciprocal_rank_fusion([["a", "b", "a"]])
    got = dict(result)
    assert math.isclose(got["a"], 1.0 / (K + 1), rel_tol=1e-12)
    assert math.isclose(got["b"], 1.0 / (K + 2), rel_tol=1e-12)
    assert [item for item, _ in result] == ["a", "b"]


def test_ties_broken_by_first_occurrence_order() -> None:
    result = reciprocal_rank_fusion([["a", "b"], ["b", "a"]])
    a_score = _score(1, 2)
    b_score = _score(2, 1)
    assert math.isclose(a_score, b_score, rel_tol=1e-12)
    assert [item for item, _ in result] == ["a", "b"]


def test_custom_k_changes_scores() -> None:
    result = reciprocal_rank_fusion([["x"]], k=10)
    [(item, score)] = result
    assert item == "x"
    assert math.isclose(score, 1.0 / 11, rel_tol=1e-12)
