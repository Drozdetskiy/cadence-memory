"""`cadence-memory hooks` sub-app: install git hooks for the master wiki."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer

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


def _fail(message: str, code: int = 1) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


@hooks_app.command("install")
def cmd_install(
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
    try:
        wiki_dir = resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())
    except WikiNotFoundError as exc:
        _fail(str(exc))

    try:
        outcome = install_post_commit_hook(wiki_dir, force=force)
    except HookInstallError as exc:
        _fail(str(exc))

    if outcome is InstallOutcome.INSTALLED:
        typer.echo(f"installed: {_HOOK_DISPLAY}")
    elif outcome is InstallOutcome.ALREADY_PRESENT:
        typer.echo(f"already installed: {_HOOK_DISPLAY}")
    else:
        _fail(
            f"refused: {_HOOK_DISPLAY} has foreign content (use --force to overwrite)",
            code=2,
        )


__all__ = ["hooks_app"]
