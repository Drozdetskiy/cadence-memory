"""Reciprocal Rank Fusion utility for merging multiple ranked lists."""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["reciprocal_rank_fusion"]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = 60
) -> list[tuple[str, float]]:
    """Merge ranked lists via Reciprocal Rank Fusion.

    Each item's score is the sum of ``1 / (k + rank)`` across all rankings,
    where ``rank`` starts at 1 and refers to the first occurrence within a
    single ranking. The returned list is sorted by score descending; ties
    are broken by first-occurrence order across the input rankings to keep
    output deterministic. Empty inner rankings are silently ignored.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    counter = 0
    for ranking in rankings:
        seen_in_ranking: set[str] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen_in_ranking:
                continue
            seen_in_ranking.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            if item not in first_seen:
                first_seen[item] = counter
                counter += 1
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
