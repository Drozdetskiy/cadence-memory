"""`cadence-memory worker` sub-app: one-shot ingest run (design2 §6.1, §11)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from cadence_memory.config.errors import ConfigError
from cadence_memory.config.loader import load_config
from cadence_memory.config.schema import Config
from cadence_memory.executor.runner import DefaultClaudeRunner
from cadence_memory.git.cache import DefaultGitCache
from cadence_memory.wiki import WikiNotFoundError
from cadence_memory.wiki.locator import CONFIG_FILENAME, resolve_wiki_dir
from cadence_memory.worker.daemon import DaemonSignals, run_daemon
from cadence_memory.worker.lock import WorkerBusyError, worker_lock
from cadence_memory.worker.run import run_pending
from cadence_memory.worker.state import StateError, load_state

worker_app = typer.Typer(
    name="worker",
    help="Background ingest worker.",
    no_args_is_help=True,
)


def _resolve_wiki_dir(wiki: Path | None) -> Path:
    return resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())


def _fail(message: str, *, code: int = 1) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


@worker_app.command("run")
def cmd_run(
    mode: Annotated[
        str,
        typer.Option("--mode", help="Run mode: 'commits' (default) or 'bootstrap'."),
    ] = "commits",
    only: Annotated[
        str | None,
        typer.Option("--only", help="Limit the run to a single repo by name."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Global cap on ingested events across all repos."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Plan only — do not call Claude or persist state."),
    ] = False,
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Walk pending commits and ingest them via Claude."""
    if mode == "bootstrap":
        _fail("bootstrap mode not yet implemented (task 1015)", code=2)
    if mode != "commits":
        _fail("--mode must be 'commits' or 'bootstrap'", code=2)

    try:
        wiki_dir = _resolve_wiki_dir(wiki)
    except WikiNotFoundError as exc:
        _fail(str(exc))

    try:
        config = load_config(wiki_dir / CONFIG_FILENAME)
    except ConfigError as exc:
        _fail(str(exc))

    state_path = wiki_dir / ".cadence-memory" / "state.json"
    try:
        state = load_state(state_path)
    except StateError as exc:
        _fail(str(exc))

    cache = DefaultGitCache(root=wiki_dir / ".cadence-memory" / "git_cache")
    runner = DefaultClaudeRunner()
    lock_path = wiki_dir / ".cadence-memory" / "worker.lock"

    try:
        with worker_lock(lock_path):
            _, summary = run_pending(
                wiki_dir=wiki_dir,
                config=config,
                state=state,
                cache=cache,
                runner=runner,
                only_repo=only,
                limit=limit,
                dry_run=dry_run,
            )
    except WorkerBusyError as exc:
        _fail(str(exc))

    typer.echo(
        f"processed {summary.events_processed}, "
        f"failed {summary.events_failed}, "
        f"${summary.cost_usd_total:.2f}"
    )
    if summary.events_failed:
        raise typer.Exit(code=1)


@worker_app.command("daemon")
def cmd_daemon(
    once: Annotated[
        bool,
        typer.Option("--once", help="Run a single tick (equivalent to `worker run`) and exit."),
    ] = False,
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Long-running daemon that polls for new commits on a fixed interval."""
    if once:
        cmd_run(mode="commits", only=None, limit=None, dry_run=False, wiki=wiki)
        return

    try:
        wiki_dir = _resolve_wiki_dir(wiki)
    except WikiNotFoundError as exc:
        _fail(str(exc))

    try:
        config = load_config(wiki_dir / CONFIG_FILENAME)
    except ConfigError as exc:
        _fail(str(exc))

    def _config_factory() -> Config:
        return load_config(wiki_dir / CONFIG_FILENAME)

    def _cache_factory() -> DefaultGitCache:
        return DefaultGitCache(root=wiki_dir / ".cadence-memory" / "git_cache")

    signals = DaemonSignals()
    run_daemon(
        wiki_dir=wiki_dir,
        poll_interval_s=config.worker.poll_interval_s,
        config_factory=_config_factory,
        state_path=wiki_dir / ".cadence-memory" / "state.json",
        cache_factory=_cache_factory,
        runner_factory=DefaultClaudeRunner,
        signals=signals,
    )

    if signals.interrupt.is_set():
        raise typer.Exit(code=130)


__all__ = ["worker_app"]
