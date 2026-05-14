"""`cadence-memory hooks` sub-app: install git hooks for the master wiki."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from cadence_memory.cli_state import get_overrides, make_logger
from cadence_memory.progress.logger import Logger
from cadence_memory.wiki import (
    HookInstallError,
    InstallOutcome,
    WikiNotFoundError,
    install_post_commit_hook,
)
from cadence_memory.wiki.locator import resolve_wiki_dir

hooks_app = typer.Typer(
    name="hooks",
    help="Manage git hooks for the master wiki.",
    no_args_is_help=True,
)

_HOOK_DISPLAY = ".git/hooks/post-commit"


@hooks_app.command("install")
def cmd_install(
    ctx: typer.Context,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing foreign hook."),
    ] = False,
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Install the qmd re-index post-commit hook into the master wiki."""
    logger: Logger = make_logger(None, get_overrides(ctx))

    try:
        wiki_dir = resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())
    except WikiNotFoundError as exc:
        logger.error("%s", str(exc))
        raise typer.Exit(code=1) from exc

    try:
        outcome = install_post_commit_hook(wiki_dir, force=force)
    except HookInstallError as exc:
        logger.error("%s", str(exc))
        raise typer.Exit(code=1) from exc

    if outcome is InstallOutcome.INSTALLED:
        logger.info("installed: %s", _HOOK_DISPLAY)
    elif outcome is InstallOutcome.ALREADY_PRESENT:
        logger.info("already installed: %s", _HOOK_DISPLAY)
    else:
        logger.error("refused: %s has foreign content (use --force to overwrite)", _HOOK_DISPLAY)
        raise typer.Exit(code=2)


__all__ = ["hooks_app"]
