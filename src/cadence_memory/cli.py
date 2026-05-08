from typing import Annotated

import typer

from cadence_memory import __version__

app = typer.Typer(
    name="cadence-memory",
    help=(
        "LLM-maintained knowledge base for Claude Code (rewrite in progress — see docs/design2.md)."
    ),
    no_args_is_help=False,
)


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


if __name__ == "__main__":
    app()
