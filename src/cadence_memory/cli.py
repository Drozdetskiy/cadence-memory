"""Typer entrypoint for the cadence-memory CLI."""

from typing import Annotated

import typer

from cadence_memory import __version__

app = typer.Typer(add_completion=False)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cadence-memory {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the cadence-memory version and exit.",
        ),
    ] = False,
) -> None:
    """cadence-memory: knowledge-base CLI complementing cadence."""
