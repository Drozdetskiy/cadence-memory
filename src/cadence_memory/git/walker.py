"""Walk pending commits as ingest events (design2 §6.1 step 3)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from cadence_memory.git.cache import CommitInfo, GitCache


@dataclass(frozen=True, slots=True)
class SingleCommitEvent:
    repo_name: str
    commit: CommitInfo


@dataclass(frozen=True, slots=True)
class NoiseBatchEvent:
    repo_name: str
    commits: tuple[CommitInfo, ...]

    @property
    def head_sha(self) -> str:
        return self.commits[-1].sha


type IngestEvent = SingleCommitEvent | NoiseBatchEvent


def _compile_any(patterns: tuple[str, ...]) -> re.Pattern[str] | None:
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns))


def iter_pending_commits(
    *,
    cache: GitCache,
    repo_name: str,
    branch: str,
    since_sha: str | None,
    skip_patterns: tuple[str, ...],
    noise_patterns: tuple[str, ...],
    limit: int | None = None,
) -> Iterator[IngestEvent]:
    skip_re = _compile_any(skip_patterns)
    noise_re = _compile_any(noise_patterns)

    pending = cache.list_commits(
        name=repo_name,
        since_sha=since_sha,
        branch=branch,
        reverse=True,
    )

    batch: list[CommitInfo] = []
    emitted = 0

    def _flush_batch() -> Iterator[IngestEvent]:
        nonlocal batch
        if batch:
            yield NoiseBatchEvent(repo_name=repo_name, commits=tuple(batch))
            batch = []

    for commit in pending:
        if skip_re is not None and skip_re.match(commit.subject):
            continue
        if noise_re is not None and noise_re.match(commit.subject):
            batch.append(commit)
            continue
        for event in _flush_batch():
            yield event
            emitted += 1
            if limit is not None and emitted >= limit:
                return
        yield SingleCommitEvent(repo_name=repo_name, commit=commit)
        emitted += 1
        if limit is not None and emitted >= limit:
            return

    for event in _flush_batch():
        yield event
        emitted += 1
        if limit is not None and emitted >= limit:
            return


def event_head_sha(event: IngestEvent) -> str:
    match event:
        case SingleCommitEvent(commit=c):
            return c.sha
        case NoiseBatchEvent() as nb:
            return nb.head_sha


__all__ = [
    "IngestEvent",
    "NoiseBatchEvent",
    "SingleCommitEvent",
    "event_head_sha",
    "iter_pending_commits",
]
