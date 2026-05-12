"""Git client Protocol and commit walker (populated by tasks 1008/1009)."""

from __future__ import annotations

from cadence_memory.git.cache import CloneResult, CommitInfo, DefaultGitCache, GitCache
from cadence_memory.git.errors import GitError, HistoryRewrittenError

__all__ = [
    "CloneResult",
    "CommitInfo",
    "DefaultGitCache",
    "GitCache",
    "GitError",
    "HistoryRewrittenError",
]
