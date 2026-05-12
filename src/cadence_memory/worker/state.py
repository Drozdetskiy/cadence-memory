"""Persistent per-repo worker state in `.cadence-memory/state.json` (design2 §6.2)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CURRENT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RepoState:
    last_sha: str | None = None
    last_run_at: datetime | None = None
    commits_processed: int = 0
    last_failure: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerState:
    version: int = CURRENT_SCHEMA_VERSION
    repos: dict[str, RepoState] = field(default_factory=dict)


class StateError(Exception):
    """Raised when the on-disk state file is malformed or unreadable."""


def _dt_to_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetimes are not allowed in worker state")
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _str_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _migrate(data: dict[str, Any], *, from_version: int) -> dict[str, Any]:
    raise NotImplementedError(f"no migration registered for schema v{from_version}")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": CURRENT_SCHEMA_VERSION, "repos": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"state file {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise StateError(f"state file {path} must contain a JSON object at the top level")
    version = raw.get("version")
    if version is None:
        raise StateError(f"state file {path} is missing the required 'version' field")
    if isinstance(version, bool) or not isinstance(version, int):
        raise StateError(f"state file {path} has non-integer 'version': {version!r}")
    if version > CURRENT_SCHEMA_VERSION:
        raise StateError(
            f"state file {path} has schema v{version} but this build only understands "
            f"v{CURRENT_SCHEMA_VERSION}; upgrade cadence-memory"
        )
    if version < CURRENT_SCHEMA_VERSION:
        return _migrate(raw, from_version=version)
    return raw


def _deserialize(data: dict[str, Any]) -> WorkerState:
    repos_raw = data.get("repos", {})
    if not isinstance(repos_raw, dict):
        raise StateError(f"'repos' must be a JSON object, got {type(repos_raw).__name__}")
    repos: dict[str, RepoState] = {}
    for name, entry in repos_raw.items():
        if not isinstance(name, str):
            raise StateError(f"repo key must be a string, got {type(name).__name__}")
        if not isinstance(entry, dict):
            raise StateError(
                f"repo entry for {name!r} must be a JSON object, got {type(entry).__name__}"
            )
        last_run_at_raw = entry.get("last_run_at")
        if last_run_at_raw is None:
            last_run_at: datetime | None = None
        elif isinstance(last_run_at_raw, str):
            try:
                last_run_at = _str_to_dt(last_run_at_raw)
            except ValueError as exc:
                raise StateError(
                    f"repo {name!r} 'last_run_at' is not a valid ISO-8601 datetime: {exc}"
                ) from exc
            if last_run_at.tzinfo is None:
                raise StateError(
                    f"repo {name!r} 'last_run_at' is missing timezone info; expected UTC offset"
                )
        else:
            raise StateError(
                f"repo {name!r} 'last_run_at' must be string or null, "
                f"got {type(last_run_at_raw).__name__}"
            )
        last_sha = entry.get("last_sha")
        if last_sha is not None and not isinstance(last_sha, str):
            raise StateError(
                f"repo {name!r} 'last_sha' must be string or null, got {type(last_sha).__name__}"
            )
        commits_processed = entry.get("commits_processed", 0)
        if isinstance(commits_processed, bool) or not isinstance(commits_processed, int):
            raise StateError(
                f"repo {name!r} 'commits_processed' must be an integer, "
                f"got {type(commits_processed).__name__}"
            )
        last_failure = entry.get("last_failure")
        if last_failure is not None and not isinstance(last_failure, str):
            raise StateError(
                f"repo {name!r} 'last_failure' must be string or null, "
                f"got {type(last_failure).__name__}"
            )
        repos[name] = RepoState(
            last_sha=last_sha,
            last_run_at=last_run_at,
            commits_processed=commits_processed,
            last_failure=last_failure,
        )
    version = data.get("version", CURRENT_SCHEMA_VERSION)
    if not isinstance(version, int):
        raise StateError(f"'version' must be an integer, got {type(version).__name__}")
    return WorkerState(version=version, repos=repos)


def load_state(path: Path) -> WorkerState:
    return _deserialize(_load_json(path))


def _serialize(state: WorkerState) -> dict[str, Any]:
    repos: dict[str, Any] = {}
    for name, repo in state.repos.items():
        repos[name] = {
            "last_sha": repo.last_sha,
            "last_run_at": _dt_to_str(repo.last_run_at) if repo.last_run_at is not None else None,
            "commits_processed": repo.commits_processed,
            "last_failure": repo.last_failure,
        }
    return {"version": state.version, "repos": repos}


def save_state(path: Path, state: WorkerState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_serialize(state), indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    try:
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass
    os.replace(tmp, path)


_UNSET: Any = ...


def update_repo(
    state: WorkerState,
    *,
    name: str,
    last_sha: str | None = _UNSET,
    last_run_at: datetime | None = _UNSET,
    commits_processed_delta: int = 0,
    last_failure: str | None = _UNSET,
) -> WorkerState:
    existing = state.repos.get(name, RepoState())
    updated = RepoState(
        last_sha=existing.last_sha if last_sha is _UNSET else last_sha,
        last_run_at=existing.last_run_at if last_run_at is _UNSET else last_run_at,
        commits_processed=existing.commits_processed + commits_processed_delta,
        last_failure=existing.last_failure if last_failure is _UNSET else last_failure,
    )
    new_repos = dict(state.repos)
    new_repos[name] = updated
    return WorkerState(version=state.version, repos=new_repos)


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "RepoState",
    "StateError",
    "WorkerState",
    "load_state",
    "save_state",
    "update_repo",
]
