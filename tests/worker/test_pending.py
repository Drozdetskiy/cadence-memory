"""Tests for `count_pending` (design2 §11)."""

from __future__ import annotations

from dataclasses import dataclass, field

from cadence_memory.git.cache import CloneResult, CommitInfo
from cadence_memory.git.errors import HistoryRewrittenError
from cadence_memory.worker.pending import count_pending


@dataclass
class _ListCommitsCall:
    name: str
    since_sha: str | None
    branch: str
    reverse: bool


@dataclass
class _FakeGitCache:
    commits: tuple[CommitInfo, ...] = ()
    raise_history_rewritten: bool = False
    calls: list[_ListCommitsCall] = field(default_factory=list)

    def ensure(
        self, *, name: str, url: str, branch: str
    ) -> CloneResult:  # pragma: no cover - unused
        raise NotImplementedError

    def head(self, *, name: str, branch: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:  # pragma: no cover - unused
        raise NotImplementedError

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:  # pragma: no cover - unused
        raise NotImplementedError

    def diff(self, *, name: str, sha: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:  # pragma: no cover - unused
        raise NotImplementedError

    def list_commits(
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]:
        self.calls.append(
            _ListCommitsCall(
                name=name,
                since_sha=since_sha,
                branch=branch,
                reverse=reverse,
            )
        )
        if self.raise_history_rewritten:
            raise HistoryRewrittenError(f"{since_sha} not an ancestor")
        return self.commits


def _commit(sha: str) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject="do thing",
        body="",
        author_date_iso="2026-05-12T12:00:00+00:00",
        parents=(),
    )


def test_count_pending_zero_when_caught_up() -> None:
    cache = _FakeGitCache(commits=())
    result = count_pending(
        cache=cache,
        repo_name="project-a",
        branch="main",
        since_sha="abc123",
    )
    assert result == 0


def test_count_pending_positive() -> None:
    cache = _FakeGitCache(
        commits=tuple(_commit(f"sha{i:040d}") for i in range(5)),
    )
    result = count_pending(
        cache=cache,
        repo_name="project-a",
        branch="main",
        since_sha="abc123",
    )
    assert result == 5


def test_count_pending_history_rewritten() -> None:
    cache = _FakeGitCache(raise_history_rewritten=True)
    result = count_pending(
        cache=cache,
        repo_name="project-a",
        branch="main",
        since_sha="abc123",
    )
    assert result == -1


def test_count_pending_since_none_means_full_branch() -> None:
    cache = _FakeGitCache(commits=())
    count_pending(
        cache=cache,
        repo_name="project-a",
        branch="main",
        since_sha=None,
    )
    assert len(cache.calls) == 1
    assert cache.calls[0].since_sha is None


def test_count_pending_passes_reverse_false() -> None:
    cache = _FakeGitCache(commits=())
    count_pending(
        cache=cache,
        repo_name="project-a",
        branch="main",
        since_sha="abc123",
    )
    assert len(cache.calls) == 1
    assert cache.calls[0].reverse is False
    assert cache.calls[0].name == "project-a"
    assert cache.calls[0].branch == "main"
