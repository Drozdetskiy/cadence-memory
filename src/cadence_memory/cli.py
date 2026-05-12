import os
from pathlib import Path
from typing import Annotated

import typer

from cadence_memory import __version__
from cadence_memory.cli_commands.repos import repos_app
from cadence_memory.cli_commands.worker import cmd_run, worker_app
from cadence_memory.config.errors import ConfigError
from cadence_memory.config.loader import load_config
from cadence_memory.executor.runner import DefaultClaudeRunner
from cadence_memory.wiki import WikiNotFoundError, scaffold_wiki
from cadence_memory.wiki.locator import CONFIG_FILENAME, resolve_wiki_dir
from cadence_memory.worker.manual import ingest_file

app = typer.Typer(
    name="cadence-memory",
    help=(
        "LLM-maintained knowledge base for Claude Code (rewrite in progress — see docs/design2.md)."
    ),
    no_args_is_help=False,
)
app.add_typer(repos_app)
app.add_typer(worker_app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cadence-memory {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """cadence-memory CLI skeleton. See docs/design2.md and docs/features.md."""


@app.command()
def init(
    target: Annotated[
        Path | None,
        typer.Argument(
            help="Directory to scaffold the master-wiki repo into (defaults to cwd).",
        ),
    ] = None,
) -> None:
    """Scaffold a master-wiki repo with seed config, CLAUDE.md, index/log/gaps pages."""
    resolved = (target or Path.cwd()).resolve()
    result = scaffold_wiki(resolved)

    for path in result.created_files:
        display = path.relative_to(resolved) if path.is_relative_to(resolved) else path
        typer.echo(f"created: {display}")
    for path in result.skipped_files:
        display = path.relative_to(resolved) if path.is_relative_to(resolved) else path
        typer.echo(f"exists: {display}")
    if result.git_initialized:
        typer.echo("git initialized")


@app.command("bootstrap")
def cmd_bootstrap(
    repo: Annotated[
        str,
        typer.Argument(help="Name of the repo (from cadence-memory.toml) to bootstrap."),
    ],
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help=(
                "Leave last_sha unchanged when any stage failed "
                "(default advances to HEAD even on partial failure)."
            ),
        ),
    ] = False,
) -> None:
    """Run the 5-stage initial bootstrap for a single repo.

    Alias for 'worker run --mode bootstrap --only <repo>'.
    """
    cmd_run(
        mode="bootstrap",
        only=repo,
        limit=None,
        dry_run=False,
        wiki=wiki,
        strict=strict,
    )


@app.command("ingest")
def cmd_ingest(
    source: Annotated[
        Path,
        typer.Argument(
            help="Path to the source file to ingest (typically under raw/notes/).",
        ),
    ],
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Ingest a single non-commit source (article, meeting notes, spec) into the master wiki."""
    try:
        wiki_dir = resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())
    except WikiNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if not source.is_absolute():
        source = (wiki_dir / source).resolve()

    if not source.is_file():
        typer.echo(f"error: source file not found: {source}", err=True)
        raise typer.Exit(code=2)

    if not source.is_relative_to(wiki_dir):
        typer.echo(f"warning: source is outside wiki: {source}", err=True)

    try:
        cfg = load_config(wiki_dir / CONFIG_FILENAME)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    outcome = ingest_file(
        source_path=source,
        config=cfg,
        wiki_dir=wiki_dir,
        runner=DefaultClaudeRunner(),
    )

    if not outcome.success:
        typer.echo(f"failed: {outcome.error or 'claude run failed'}", err=True)
        raise typer.Exit(code=1)

    cost = f"${outcome.cost_usd:.2f}" if outcome.cost_usd is not None else "(n/a)"
    sha_display = outcome.wiki_commit_sha if outcome.wiki_commit_sha is not None else "no changes"
    typer.echo(f"ok: {sha_display} ({len(outcome.pages_touched)} pages, {cost})")


if __name__ == "__main__":
    app()
