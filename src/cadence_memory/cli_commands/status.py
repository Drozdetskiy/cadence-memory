"""Status command — print at-a-glance worker state for every tracked repo (design2 §11)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from cadence_memory.config.errors import ConfigError
from cadence_memory.config.loader import load_config
from cadence_memory.config.schema import Config
from cadence_memory.git.cache import DefaultGitCache, GitCache
from cadence_memory.git.errors import GitError
from cadence_memory.wiki import WikiNotFoundError
from cadence_memory.wiki.locator import CONFIG_FILENAME, resolve_wiki_dir
from cadence_memory.worker.pending import count_pending
from cadence_memory.worker.state import RepoState, StateError, WorkerState, load_state

_FAILURE_TABLE_MAX = 40
_FAILURE_SHORT_MAX = 60
_VALID_FORMATS = ("table", "json")


@dataclass(frozen=True, slots=True)
class RepoStatusRow:
    name: str
    branch: str
    last_sha: str | None
    pending: int | None
    last_run_at: datetime | None
    commits_processed: int
    last_failure: str | None


def build_rows(*, config: Config, state: WorkerState, cache: GitCache) -> tuple[RepoStatusRow, ...]:
    rows: list[RepoStatusRow] = []
    for repo_cfg in config.repos:
        existing = state.repos.get(repo_cfg.name, RepoState())
        local_sha = cache.head_local(name=repo_cfg.name, branch=repo_cfg.branch)
        if local_sha is None:
            pending: int | None = None
        else:
            try:
                pending = count_pending(
                    cache=cache,
                    repo_name=repo_cfg.name,
                    branch=repo_cfg.branch,
                    since_sha=existing.last_sha or repo_cfg.start_commit,
                )
            except GitError:
                pending = -2
        rows.append(
            RepoStatusRow(
                name=repo_cfg.name,
                branch=repo_cfg.branch,
                last_sha=existing.last_sha,
                pending=pending,
                last_run_at=existing.last_run_at,
                commits_processed=existing.commits_processed,
                last_failure=existing.last_failure,
            )
        )
    return tuple(rows)


def _collapse_and_truncate(msg: str, max_len: int) -> str:
    collapsed = msg.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    while "  " in collapsed:
        collapsed = collapsed.replace("  ", " ")
    if len(collapsed) > max_len:
        collapsed = collapsed[:max_len] + "…"
    return collapsed


def render_table(rows: tuple[RepoStatusRow, ...]) -> str:
    headers = ("REPO", "BRANCH", "LAST_SHA", "PENDING", "LAST_RUN", "FAILURE")
    data_rows: list[tuple[str, str, str, str, str, str]] = []
    for row in rows:
        sha_cell = row.last_sha[:7] if row.last_sha else "—"
        if row.pending is None:
            pending_cell = "?"
        elif row.pending == -1:
            pending_cell = "!hist"
        elif row.pending == -2:
            pending_cell = "!git"
        else:
            pending_cell = str(row.pending)
        if row.last_run_at is not None:
            last_run_cell = row.last_run_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        else:
            last_run_cell = "—"
        failure_cell = (
            _collapse_and_truncate(row.last_failure, _FAILURE_TABLE_MAX)
            if row.last_failure is not None
            else "—"
        )
        data_rows.append(
            (row.name, row.branch, sha_cell, pending_cell, last_run_cell, failure_cell)
        )

    widths = [len(h) for h in headers]
    for data_row in data_rows:
        for i, value in enumerate(data_row):
            if len(value) > widths[i]:
                widths[i] = len(value)

    def _render(row: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[i]) for i, value in enumerate(row)).rstrip()

    lines = [_render(headers)]
    for data_row in data_rows:
        lines.append(_render(data_row))
    return "\n".join(lines)


def render_short(rows: tuple[RepoStatusRow, ...]) -> str:
    if not rows:
        return "cadence-memory: 0 repos configured. Run `cadence-memory repos add <name> <url>`"

    n_repos = len(rows)
    n_pending: int = 0
    for r in rows:
        if r.pending is not None and r.pending >= 0:
            n_pending += r.pending
    failing = [r for r in rows if r.last_failure is not None]
    n_failures = len(failing)

    line = (
        f"cadence-memory: {n_repos} repos,"
        f" {n_pending} pending commits,"
        f" {n_failures} recent failures"
    )

    if n_failures >= 1:
        first = failing[0]
        msg = _collapse_and_truncate(first.last_failure or "", _FAILURE_SHORT_MAX)
        line += f" ({first.name}: {msg})"
        if n_failures > 1:
            line += ", …"

    if any(r.pending is None for r in rows):
        line += " (some repos not cloned)"
    if any(r.pending == -1 for r in rows):
        line += " (some repos: history rewritten)"
    if any(r.pending == -2 for r in rows):
        line += " (some repos: git error)"

    return line


def render_json(wiki_dir: Path, rows: tuple[RepoStatusRow, ...]) -> str:
    repos_list: list[dict[str, object]] = []
    for row in rows:
        last_run_str: str | None = None
        if row.last_run_at is not None:
            last_run_str = row.last_run_at.astimezone(UTC).isoformat(timespec="seconds")
        repos_list.append(
            {
                "name": row.name,
                "branch": row.branch,
                "last_sha": row.last_sha,
                "pending": row.pending,
                "last_run_at": last_run_str,
                "commits_processed": row.commits_processed,
                "last_failure": row.last_failure,
            }
        )
    return json.dumps({"wiki": str(wiki_dir), "repos": repos_list}, indent=2)


def cmd_status(
    short: Annotated[
        bool,
        typer.Option("--short", help="Emit a single summary line (used by SessionStart hook)."),
    ] = False,
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: table or json."),
    ] = "table",
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Print at-a-glance worker state for every tracked repo."""
    if format not in _VALID_FORMATS:
        typer.echo(
            f"error: --format must be one of {', '.join(_VALID_FORMATS)} (got {format!r})",
            err=True,
        )
        raise typer.Exit(code=2)

    if short and format == "json":
        typer.echo(
            "error: --short and --format json are incompatible output contracts",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        wiki_dir = resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())
    except WikiNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    try:
        cfg = load_config(wiki_dir / CONFIG_FILENAME)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    state_path = wiki_dir / ".cadence-memory" / "state.json"
    try:
        state = load_state(state_path)
    except StateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    cache: GitCache = DefaultGitCache(root=wiki_dir / ".cadence-memory" / "git_cache")
    rows = build_rows(config=cfg, state=state, cache=cache)

    if short:
        typer.echo(render_short(rows))
    elif format == "json":
        typer.echo(render_json(wiki_dir, rows))
    else:
        typer.echo(render_table(rows))


__all__ = [
    "RepoStatusRow",
    "build_rows",
    "cmd_status",
    "render_json",
    "render_short",
    "render_table",
]
