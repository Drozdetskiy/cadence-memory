"""Count pending commits for status reporting (design2 §11)."""

from __future__ import annotations

from cadence_memory.git.cache import GitCache
from cadence_memory.git.errors import HistoryRewrittenError


def count_pending(
    *,
    cache: GitCache,
    repo_name: str,
    branch: str,
    since_sha: str | None,
) -> int:
    try:
        commits = cache.list_commits(
            name=repo_name,
            since_sha=since_sha,
            branch=branch,
            reverse=False,
        )
    except HistoryRewrittenError:
        return -1
    return len(commits)


__all__ = ["count_pending"]
