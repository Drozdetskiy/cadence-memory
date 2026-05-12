"""Tests for the git walker (design2 §6.1 step 3)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from cadence_memory.git import (
    CommitInfo,
    IngestEvent,
    NoiseBatchEvent,
    SingleCommitEvent,
    event_head_sha,
    iter_pending_commits,
)


class FakeGitCache:
    """Pure-Python fake implementing the slice of `GitCache` used by the walker."""

    def __init__(self, commits: tuple[CommitInfo, ...]) -> None:
        self._commits = commits
        self.list_commits_calls: list[dict[str, Any]] = []

    def list_commits(
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]:
        self.list_commits_calls.append(
            {
                "name": name,
                "since_sha": since_sha,
                "branch": branch,
                "reverse": reverse,
            }
        )
        return self._commits

    def ensure(self, *, name: str, url: str, branch: str) -> Any:
        raise NotImplementedError

    def head(self, *, name: str, branch: str) -> str:
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:
        raise NotImplementedError

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:
        raise NotImplementedError

    def diff(self, *, name: str, sha: str) -> str:
        raise NotImplementedError

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:
        raise NotImplementedError


def _mk(
    subject: str,
    *,
    sha: str = "a" * 40,
    short: str = "aaaaaaa",
    body: str = "",
    author_date_iso: str = "2026-05-12T00:00:00+00:00",
    parents: tuple[str, ...] = (),
) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=short,
        subject=subject,
        body=body,
        author_date_iso=author_date_iso,
        parents=parents,
    )


def _events(commits: tuple[CommitInfo, ...], **kwargs: Any) -> list[IngestEvent]:
    cache = FakeGitCache(commits)
    return list(
        iter_pending_commits(
            cache=cache,
            repo_name=kwargs.pop("repo_name", "repo"),
            branch=kwargs.pop("branch", "main"),
            since_sha=kwargs.pop("since_sha", None),
            skip_patterns=kwargs.pop("skip_patterns", ()),
            noise_patterns=kwargs.pop("noise_patterns", ()),
            **kwargs,
        )
    )


def test_empty_range_yields_nothing() -> None:
    assert _events(()) == []


def test_single_normal_commit() -> None:
    commit = _mk("Add feature", sha="1" * 40, short="1111111")
    events = _events((commit,), repo_name="my-repo")
    assert events == [SingleCommitEvent(repo_name="my-repo", commit=commit)]


def test_skip_drops_commit() -> None:
    skipped = _mk("Merge pull request #1", sha="s" * 40)
    real = _mk("Real change", sha="r" * 40)
    events = _events((skipped, real), skip_patterns=("^Merge ",))
    assert events == [SingleCommitEvent(repo_name="repo", commit=real)]


def test_noise_batched() -> None:
    n1 = _mk("Bump dep X", sha="1" * 40)
    n2 = _mk("Bump dep Y", sha="2" * 40)
    n3 = _mk("Bump dep Z", sha="3" * 40)
    events = _events((n1, n2, n3), noise_patterns=("^Bump ",))
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, NoiseBatchEvent)
    assert event.repo_name == "repo"
    assert event.commits == (n1, n2, n3)
    assert event.head_sha == n3.sha


def test_noise_split_by_real_commit() -> None:
    n1 = _mk("Bump dep A", sha="1" * 40)
    n2 = _mk("Bump dep B", sha="2" * 40)
    r1 = _mk("Real one", sha="r" * 40)
    n3 = _mk("Bump dep C", sha="3" * 40)
    r2 = _mk("Real two", sha="R" * 40)
    events = _events((n1, n2, r1, n3, r2), noise_patterns=("^Bump ",))
    assert events == [
        NoiseBatchEvent(repo_name="repo", commits=(n1, n2)),
        SingleCommitEvent(repo_name="repo", commit=r1),
        NoiseBatchEvent(repo_name="repo", commits=(n3,)),
        SingleCommitEvent(repo_name="repo", commit=r2),
    ]


def test_skip_then_noise_still_batches() -> None:
    skipped = _mk("Merge pull request", sha="s" * 40)
    n1 = _mk("Bump A", sha="1" * 40)
    n2 = _mk("Bump B", sha="2" * 40)
    real = _mk("Real change", sha="r" * 40)
    events = _events(
        (skipped, n1, n2, real),
        skip_patterns=("^Merge ",),
        noise_patterns=("^Bump ",),
    )
    assert events == [
        NoiseBatchEvent(repo_name="repo", commits=(n1, n2)),
        SingleCommitEvent(repo_name="repo", commit=real),
    ]


def test_limit_caps_event_count() -> None:
    commits = tuple(_mk(f"Real {i}", sha=str(i) * 40) for i in range(6))
    cache = FakeGitCache(commits)
    iterator = iter_pending_commits(
        cache=cache,
        repo_name="repo",
        branch="main",
        since_sha=None,
        skip_patterns=(),
        noise_patterns=(),
        limit=3,
    )
    events = list(iterator)
    assert len(events) == 3
    assert [e.commit.sha for e in events if isinstance(e, SingleCommitEvent)] == [
        commits[0].sha,
        commits[1].sha,
        commits[2].sha,
    ]


def test_limit_does_not_emit_partial_batch() -> None:
    r1 = _mk("Real change", sha="r" * 40)
    n1 = _mk("Bump A", sha="1" * 40)
    n2 = _mk("Bump B", sha="2" * 40)
    events = _events(
        (r1, n1, n2),
        noise_patterns=("^Bump ",),
        limit=1,
    )
    assert events == [SingleCommitEvent(repo_name="repo", commit=r1)]


def test_limit_cuts_at_mid_loop_batch_flush() -> None:
    n1 = _mk("Bump A", sha="1" * 40)
    n2 = _mk("Bump B", sha="2" * 40)
    r1 = _mk("Real change", sha="r" * 40)
    r2 = _mk("Another real", sha="R" * 40)
    events = _events(
        (n1, n2, r1, r2),
        noise_patterns=("^Bump ",),
        limit=1,
    )
    assert events == [NoiseBatchEvent(repo_name="repo", commits=(n1, n2))]


def test_patterns_are_start_anchored() -> None:
    leading = _mk("Bump dep X", sha="1" * 40)
    embedded = _mk("Re-Bump dep Y", sha="2" * 40)
    events = _events(
        (leading, embedded),
        noise_patterns=("Bump ",),
    )
    assert events == [
        NoiseBatchEvent(repo_name="repo", commits=(leading,)),
        SingleCommitEvent(repo_name="repo", commit=embedded),
    ]


def test_head_sha_helper() -> None:
    commit = _mk("Real change", sha="r" * 40)
    single = SingleCommitEvent(repo_name="repo", commit=commit)
    assert event_head_sha(single) == commit.sha

    n1 = _mk("Bump A", sha="1" * 40)
    n2 = _mk("Bump B", sha="2" * 40)
    batch = NoiseBatchEvent(repo_name="repo", commits=(n1, n2))
    assert event_head_sha(batch) == n2.sha


def test_compile_alternation() -> None:
    bump = _mk("Bump dep X", sha="1" * 40)
    merge = _mk("Merge pull request #42", sha="2" * 40)
    real = _mk("Real change", sha="r" * 40)
    events = _events(
        (bump, merge, real),
        skip_patterns=("^Bump ", "^Merge "),
    )
    assert events == [SingleCommitEvent(repo_name="repo", commit=real)]


def test_walker_calls_list_commits_with_expected_args() -> None:
    commit = _mk("Real", sha="r" * 40)
    cache = FakeGitCache((commit,))
    list(
        iter_pending_commits(
            cache=cache,
            repo_name="my-repo",
            branch="develop",
            since_sha="deadbeef",
            skip_patterns=(),
            noise_patterns=(),
        )
    )
    assert cache.list_commits_calls == [
        {
            "name": "my-repo",
            "since_sha": "deadbeef",
            "branch": "develop",
            "reverse": True,
        }
    ]


def test_frozen_event_dataclasses() -> None:
    commit = _mk("Real", sha="r" * 40)
    single = SingleCommitEvent(repo_name="repo", commit=commit)
    with pytest.raises(FrozenInstanceError):
        single.repo_name = "other"  # type: ignore[misc]
    batch = NoiseBatchEvent(repo_name="repo", commits=(commit,))
    with pytest.raises(FrozenInstanceError):
        batch.commits = ()  # type: ignore[misc]
