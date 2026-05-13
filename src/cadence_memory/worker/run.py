"""One-shot `run_pending` orchestrator: walk pending commits and ingest them (design2 §6.1)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from cadence_memory.config.schema import Config
from cadence_memory.executor.runner import ClaudeRunner
from cadence_memory.git.cache import GitCache
from cadence_memory.git.errors import GitError, HistoryRewrittenError
from cadence_memory.git.walker import (
    IngestEvent,
    NoiseBatchEvent,
    SingleCommitEvent,
    event_head_sha,
    iter_pending_commits,
)
from cadence_memory.worker.ingest import ingest_event
from cadence_memory.worker.state import (
    RepoState,
    WorkerState,
    save_state,
    update_repo,
)

_SUBJECT_MAX = 72
_STATE_RELPATH = Path(".cadence-memory") / "state.json"


@dataclass(frozen=True, slots=True)
class RunSummary:
    repos: tuple[str, ...]
    events_processed: int
    events_failed: int
    cost_usd_total: float


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _event_subject(event: IngestEvent) -> str:
    match event:
        case SingleCommitEvent(commit=c):
            return c.subject
        case NoiseBatchEvent() as nb:
            return nb.commits[-1].subject


def _event_tag(event: IngestEvent) -> str:
    match event:
        case SingleCommitEvent():
            return "[single]"
        case NoiseBatchEvent() as nb:
            return f"[noise:{len(nb.commits)}]"


def run_pending(
    *,
    wiki_dir: Path,
    config: Config,
    state: WorkerState,
    cache: GitCache,
    runner: ClaudeRunner,
    only_repo: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    clock: Callable[[], datetime] = _utc_now,
    out: TextIO = sys.stdout,
    should_stop_between_repos: Callable[[], bool] | None = None,
    should_stop_between_events: Callable[[], bool] | None = None,
) -> tuple[WorkerState, RunSummary]:
    state_path = wiki_dir / _STATE_RELPATH

    events_processed = 0
    events_failed = 0
    cost_total = 0.0
    visited_repos: list[str] = []
    total_emitted = 0

    if dry_run:
        out.write("plan:\n")

    per_repo_cap = config.worker.max_commits_per_run

    for repo_cfg in config.repos:
        if should_stop_between_repos is not None and should_stop_between_repos():
            break
        if only_repo is not None and repo_cfg.name != only_repo:
            continue

        remaining: int
        if limit is None:
            remaining = per_repo_cap
        else:
            global_remaining = limit - total_emitted
            if global_remaining <= 0:
                continue
            remaining = min(per_repo_cap, global_remaining)

        try:
            cache.ensure(name=repo_cfg.name, url=repo_cfg.url, branch=repo_cfg.branch)
        except GitError as exc:
            if dry_run:
                out.write(f"  {repo_cfg.name}: cache unavailable — {exc}\n")
            else:
                state = update_repo(
                    state,
                    name=repo_cfg.name,
                    last_run_at=clock(),
                    last_failure=str(exc),
                )
                save_state(state_path, state)
            continue

        existing = state.repos.get(repo_cfg.name, RepoState())
        since = existing.last_sha or repo_cfg.start_commit

        try:
            events = list(
                iter_pending_commits(
                    cache=cache,
                    repo_name=repo_cfg.name,
                    branch=repo_cfg.branch,
                    since_sha=since,
                    skip_patterns=config.worker.skip_subject_patterns,
                    noise_patterns=config.worker.noise_subject_patterns,
                    limit=remaining,
                )
            )
        except HistoryRewrittenError as exc:
            if dry_run:
                out.write(f"  {repo_cfg.name}: history rewritten — needs reset\n")
            else:
                state = update_repo(
                    state,
                    name=repo_cfg.name,
                    last_run_at=clock(),
                    last_failure=str(exc),
                )
                save_state(state_path, state)
            continue
        except GitError as exc:
            if dry_run:
                out.write(f"  {repo_cfg.name}: cache read failed — {exc}\n")
            else:
                state = update_repo(
                    state,
                    name=repo_cfg.name,
                    last_run_at=clock(),
                    last_failure=str(exc),
                )
                save_state(state_path, state)
            continue

        visited_repos.append(repo_cfg.name)

        if dry_run:
            out.write(f"  {repo_cfg.name}: {len(events)} pending events\n")
            for event in events:
                short = event_head_sha(event)[:7]
                subject = _event_subject(event)[:_SUBJECT_MAX]
                tag = _event_tag(event)
                out.write(f"    {tag} {short} — {subject}\n")
            total_emitted += len(events)
            continue

        for event in events:
            if should_stop_between_events is not None and should_stop_between_events():
                break
            outcome = ingest_event(
                event=event,
                repo_cfg=repo_cfg,
                config=config,
                wiki_dir=wiki_dir,
                cache=cache,
                runner=runner,
                clock=clock,
            )
            if outcome.cost_usd is not None:
                cost_total += outcome.cost_usd
            if outcome.success:
                state = update_repo(
                    state,
                    name=repo_cfg.name,
                    last_sha=outcome.head_sha,
                    last_run_at=clock(),
                    commits_processed_delta=1,
                    last_failure=None,
                )
                events_processed += 1
            else:
                state = update_repo(
                    state,
                    name=repo_cfg.name,
                    last_run_at=clock(),
                    last_failure=outcome.error or "unknown failure",
                )
                events_failed += 1
            total_emitted += 1
            save_state(state_path, state)
            if not outcome.success and config.worker.stop_on_failure:
                break

    if dry_run:
        out.write(f"total: {total_emitted} events, model={config.model}\n")

    summary = RunSummary(
        repos=tuple(visited_repos),
        events_processed=events_processed,
        events_failed=events_failed,
        cost_usd_total=cost_total,
    )
    return state, summary


__all__ = ["RunSummary", "run_pending"]
