"""Git cache error types (design2 §6.1 step 1)."""

from __future__ import annotations


class GitError(Exception):
    """Raised when a git subprocess fails or returns unexpected output."""


class HistoryRewrittenError(GitError):
    """Raised when a recorded sha is no longer an ancestor of the upstream branch."""


__all__ = ["GitError", "HistoryRewrittenError"]
