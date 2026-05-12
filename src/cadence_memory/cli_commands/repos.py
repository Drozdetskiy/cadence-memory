"""`cadence-memory repos` sub-app: add/list/remove tracked source repos (design2 §11)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from cadence_memory.config.errors import ConfigError
from cadence_memory.config.writer import add_repo, list_repos, remove_repo
from cadence_memory.wiki import WikiNotFoundError
from cadence_memory.wiki.locator import CONFIG_FILENAME, resolve_wiki_dir

repos_app = typer.Typer(
    name="repos",
    help="Manage tracked source repositories.",
    no_args_is_help=True,
)


def _resolve_config_path(wiki: Path | None) -> Path:
    wiki_dir = resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())
    return wiki_dir / CONFIG_FILENAME


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


@repos_app.command("add")
def cmd_add(
    name: Annotated[str, typer.Argument(help="Repo slug (used as projects/<name>/ subtree).")],
    url: Annotated[str, typer.Argument(help="Clone URL passed to `git clone`.")],
    branch: Annotated[str, typer.Option("--branch", help="Branch to track.")] = "main",
    start_commit: Annotated[
        str | None,
        typer.Option("--start-commit", help="First commit to ingest (default: branch tip)."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the wiki-wide Claude model for this repo."),
    ] = None,
    budget: Annotated[
        float | None,
        typer.Option("--budget", help="Per-repo USD budget cap (overrides wiki default)."),
    ] = None,
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Add a repo entry to the wiki's config.yaml."""
    try:
        config_path = _resolve_config_path(wiki)
        add_repo(
            config_path,
            name=name,
            url=url,
            branch=branch,
            start_commit=start_commit,
            model=model,
            budget_usd=budget,
        )
    except (ConfigError, WikiNotFoundError) as exc:
        _fail(str(exc))
    typer.echo(f"added: {name}")


@repos_app.command("list")
def cmd_list(
    format: Annotated[
        str, typer.Option("--format", help="Output format: table or json.")
    ] = "table",
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """List repos tracked in the wiki's config.yaml."""
    if format not in ("table", "json"):
        _fail("--format must be table or json")
    try:
        config_path = _resolve_config_path(wiki)
        rendered = list_repos(config_path, format="json" if format == "json" else "table")
    except (ConfigError, WikiNotFoundError) as exc:
        _fail(str(exc))
    typer.echo(rendered)


@repos_app.command("remove")
def cmd_remove(
    name: Annotated[str, typer.Argument(help="Repo slug to remove from config.yaml.")],
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Remove a repo entry from the wiki's config.yaml (state and history are preserved)."""
    try:
        config_path = _resolve_config_path(wiki)
        remove_repo(config_path, name=name)
    except (ConfigError, WikiNotFoundError) as exc:
        _fail(str(exc))
    typer.echo(f"removed: {name}")


__all__ = ["repos_app"]
