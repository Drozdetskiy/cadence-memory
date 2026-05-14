"""Progress event dataclasses for cadence-memory commands (design2 §2)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal

PhaseName = Literal[
    "bootstrap",
    "bootstrap-stage",
    "ingest",
    "lint",
    "daemon-tick",
    "manual-ingest",
    "worker-run",
    "claude",
]


def now_ts() -> str:
    """Return current UTC time as ISO-8601 string with Z suffix, no microseconds."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class PhaseStartEvent:
    phase: PhaseName
    repo: str | None = None
    stage: str | None = None
    model: str | None = None
    source: str | None = None

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": now_ts(),
            "event": "phase_start",
            "phase": self.phase,
            "repo": self.repo,
            "stage": self.stage,
            "model": self.model,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class PhaseEndEvent:
    phase: PhaseName
    duration_ms: int | None = None
    result: str | None = None
    tokens_in_total: int | None = None
    tokens_out_total: int | None = None
    cost_usd_estimate: float | None = None

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": now_ts(),
            "event": "phase_end",
            "phase": self.phase,
            "duration_ms": self.duration_ms,
            "result": self.result,
            "tokens_in_total": self.tokens_in_total,
            "tokens_out_total": self.tokens_out_total,
            "cost_usd_estimate": self.cost_usd_estimate,
        }


@dataclass(frozen=True, slots=True)
class StageStartEvent:
    stage_index: int
    stage_name: str
    repo: str

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": now_ts(),
            "event": "stage_start",
            "stage_index": self.stage_index,
            "stage_name": self.stage_name,
            "repo": self.repo,
        }


@dataclass(frozen=True, slots=True)
class StageEndEvent:
    stage_index: int
    duration_ms: int | None = None
    result: str | None = None
    pages_touched: int | None = None
    cost_usd_estimate: float | None = None

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": now_ts(),
            "event": "stage_end",
            "stage_index": self.stage_index,
            "duration_ms": self.duration_ms,
            "result": self.result,
            "pages_touched": self.pages_touched,
            "cost_usd_estimate": self.cost_usd_estimate,
        }


@dataclass(frozen=True, slots=True)
class IngestStartEvent:
    repo: str
    commit_sha: str
    subject: str

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": now_ts(),
            "event": "ingest_start",
            "repo": self.repo,
            "commit_sha": self.commit_sha,
            "subject": self.subject,
        }


@dataclass(frozen=True, slots=True)
class IngestEndEvent:
    repo: str
    commit_sha: str
    duration_ms: int | None = None
    result: str | None = None
    pages_touched: int | None = None
    cost_usd_estimate: float | None = None

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": now_ts(),
            "event": "ingest_end",
            "repo": self.repo,
            "commit_sha": self.commit_sha,
            "duration_ms": self.duration_ms,
            "result": self.result,
            "pages_touched": self.pages_touched,
            "cost_usd_estimate": self.cost_usd_estimate,
        }


@dataclass(frozen=True, slots=True)
class ClaudeProgressEvent:
    phase: str
    kind: str
    detail: str | None = None

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": now_ts(),
            "event": "claude_progress",
            "phase": self.phase,
            "kind": self.kind,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    phase: str
    message: str
    detail: str | None = None

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": now_ts(),
            "event": "error",
            "phase": self.phase,
            "message": self.message,
            "detail": self.detail,
        }


ProgressEvent = (
    PhaseStartEvent
    | PhaseEndEvent
    | StageStartEvent
    | StageEndEvent
    | IngestStartEvent
    | IngestEndEvent
    | ClaudeProgressEvent
    | ErrorEvent
)

__all__ = [
    "ClaudeProgressEvent",
    "ErrorEvent",
    "IngestEndEvent",
    "IngestStartEvent",
    "PhaseEndEvent",
    "PhaseName",
    "PhaseStartEvent",
    "ProgressEvent",
    "StageEndEvent",
    "StageStartEvent",
    "now_ts",
]
