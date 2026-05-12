"""Frozen dataclasses modelling the v2 config.yaml shape (design2 §5)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Worker tunables (design2 §5, `worker:` block)."""

    poll_interval_s: int = 3600
    noise_subject_patterns: tuple[str, ...] = ()
    skip_subject_patterns: tuple[str, ...] = ()
    max_commits_per_run: int = 50
    stop_on_failure: bool = True


@dataclass(frozen=True, slots=True)
class RepoConfig:
    """A single tracked source repo (design2 §5, `repos[]` entry)."""

    name: str
    url: str
    branch: str = "main"
    start_commit: str | None = None
    model: str | None = None
    budget_usd: float | None = None
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Config:
    """Top-level v2 config (design2 §5)."""

    model: str = "claude-sonnet-4-6"
    budget_usd: float | None = 0.50
    idle_timeout_s: int = 300
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    repos: tuple[RepoConfig, ...] = ()
    raw_auto_ingest: bool = False
