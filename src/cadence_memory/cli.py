from pathlib import Path
from typing import Annotated

import typer

from cadence_memory import __version__
from cadence_memory.cli_commands.repos import repos_app
from cadence_memory.wiki import scaffold_wiki

app = typer.Typer(
    name="cadence-memory",
    help=(
        "LLM-maintained knowledge base for Claude Code (rewrite in progress — see docs/design2.md)."
    ),
    no_args_is_help=False,
)
app.add_typer(repos_app)


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


if __name__ == "__main__":
    app()
