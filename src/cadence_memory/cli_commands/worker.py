"""`cadence-memory worker` sub-app: one-shot ingest run (design2 §6.1, §11)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from cadence_memory.cli_state import get_overrides, make_logger
from cadence_memory.config.errors import ConfigError
from cadence_memory.config.loader import load_config
from cadence_memory.config.schema import Config
from cadence_memory.executor.runner import DefaultClaudeRunner
from cadence_memory.git.cache import DefaultGitCache
from cadence_memory.progress.logger import Logger
from cadence_memory.wiki import WikiNotFoundError
from cadence_memory.wiki.locator import CONFIG_FILENAME, resolve_wiki_dir
from cadence_memory.worker.bootstrap import run_bootstrap
from cadence_memory.worker.daemon import DaemonSignals, run_daemon
from cadence_memory.worker.lock import WorkerBusyError, worker_lock
from cadence_memory.worker.run import run_pending
from cadence_memory.worker.state import StateError, load_state, save_state, update_repo

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


def _utc_now() -> datetime:
    return datetime.now(UTC)


@worker_app.command("run")
def cmd_run(
    ctx: typer.Context,
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
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help=(
                "Bootstrap mode: leave last_sha unchanged when any stage failed "
                "(default advances to HEAD even on partial failure)."
            ),
        ),
    ] = False,
) -> None:
    """Walk pending commits and ingest them via Claude."""
    if mode == "bootstrap":
        if only is None:
            _fail("error: --mode bootstrap requires --only <repo>", code=2)
    elif mode != "commits":
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

    overrides = get_overrides(ctx)
    logger: Logger = make_logger(config, overrides, wiki_dir=wiki_dir)

    cache = DefaultGitCache(root=wiki_dir / ".cadence-memory" / "git_cache")
    runner = DefaultClaudeRunner()
    lock_path = wiki_dir / ".cadence-memory" / "worker.lock"

    if mode == "bootstrap":
        assert only is not None
        repo_cfg = next((r for r in config.repos if r.name == only), None)
        if repo_cfg is None:
            _fail(f"unknown repo: {only}", code=2)

        logger.info("starting bootstrap for %s", only)

        try:
            with worker_lock(lock_path):
                outcome = run_bootstrap(
                    repo_cfg=repo_cfg,
                    config=config,
                    wiki_dir=wiki_dir,
                    cache=cache,
                    runner=runner,
                    logger=logger,
                )
        except WorkerBusyError as exc:
            _fail(str(exc))

        advance = not (strict and outcome.stages_failed)
        failure_msg = (
            "; ".join(f"bootstrap-{s}" for s in outcome.stages_failed)
            if outcome.stages_failed
            else None
        )
        if advance:
            state = update_repo(
                state,
                name=repo_cfg.name,
                last_sha=outcome.head_sha,
                last_run_at=_utc_now(),
                last_failure=failure_msg,
            )
        else:
            state = update_repo(
                state,
                name=repo_cfg.name,
                last_run_at=_utc_now(),
                last_failure=failure_msg,
            )
        save_state(state_path, state)

        logger.print(
            "bootstrap %s: stages run %d, failed %d, $%.2f",
            repo_cfg.name,
            len(outcome.stages_run),
            len(outcome.stages_failed),
            outcome.cost_usd_total,
        )
        if outcome.stages_failed:
            raise typer.Exit(code=1)
        return

    logger.info("starting worker run (mode=%s)", mode)

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
                logger=logger,
            )
    except WorkerBusyError as exc:
        _fail(str(exc))

    logger.print(
        "processed %d, failed %d, $%.2f",
        summary.events_processed,
        summary.events_failed,
        summary.cost_usd_total,
    )
    if summary.events_failed:
        raise typer.Exit(code=1)


@worker_app.command("daemon")
def cmd_daemon(
    ctx: typer.Context,
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
        cmd_run(ctx, mode="commits", only=None, limit=None, dry_run=False, wiki=wiki)
        return

    try:
        wiki_dir = _resolve_wiki_dir(wiki)
    except WikiNotFoundError as exc:
        _fail(str(exc))

    try:
        config = load_config(wiki_dir / CONFIG_FILENAME)
    except ConfigError as exc:
        _fail(str(exc))

    overrides = get_overrides(ctx)
    logger: Logger = make_logger(config, overrides, wiki_dir=wiki_dir)
    logger.info("starting daemon (poll_interval=%ds)", config.worker.poll_interval_s)

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
        logger=logger,
    )

    if signals.interrupt.is_set():
        raise typer.Exit(code=130)


__all__ = ["worker_app"]
