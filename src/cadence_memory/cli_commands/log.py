"""`cadence-memory log` sub-app: manage the master wiki activity log."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from cadence_memory.cli_state import get_overrides, make_logger
from cadence_memory.progress.logger import Logger
from cadence_memory.wiki import WikiNotFoundError
from cadence_memory.wiki.locator import resolve_wiki_dir
from cadence_memory.worker.log_rotate import LogRotationError, rotate_log

log_app = typer.Typer(
    name="log",
    help="Manage the master wiki activity log.",
    no_args_is_help=True,
)


@log_app.command("rotate")
def cmd_rotate(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would rotate without writing anything."),
    ] = False,
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Archive old log.md entries into per-month files under log/."""
    logger: Logger = make_logger(None, get_overrides(ctx))

    try:
        wiki_dir = resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())
    except WikiNotFoundError as exc:
        logger.error("%s", str(exc))
        raise typer.Exit(code=1) from exc

    try:
        outcome = rotate_log(wiki_dir=wiki_dir, dry_run=dry_run, logger=logger)
    except LogRotationError as exc:
        logger.error("%s", str(exc))
        raise typer.Exit(code=1) from exc

    if outcome.rotated:
        paths = ", ".join(str(p.relative_to(wiki_dir)) for p in outcome.archive_files)
        logger.print(
            "rotated: %d entries archived to %s (commit %s)",
            outcome.archived_entries,
            paths,
            outcome.commit_sha,
        )
    elif dry_run and outcome.archived_entries > 0:
        logger.print("planned: %d entries would archive", outcome.archived_entries)
    else:
        logger.print("no-op: nothing to rotate")


__all__ = ["log_app"]
