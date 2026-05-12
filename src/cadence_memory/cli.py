import json
import os
from pathlib import Path
from typing import Annotated

import typer

from cadence_memory import __version__
from cadence_memory.cli_commands.hooks import hooks_app
from cadence_memory.cli_commands.repos import repos_app
from cadence_memory.cli_commands.status import cmd_status
from cadence_memory.cli_commands.worker import cmd_run, worker_app
from cadence_memory.config.errors import ConfigError
from cadence_memory.config.loader import load_config
from cadence_memory.executor.runner import DefaultClaudeRunner
from cadence_memory.search import (
    Hit,
    NoBackendAvailableError,
    QmdBackend,
    RipgrepBackend,
    SearchBackend,
    SearchError,
    pick_backend,
)
from cadence_memory.wiki import WikiNotFoundError, scaffold_wiki
from cadence_memory.wiki.locator import CONFIG_FILENAME, resolve_wiki_dir
from cadence_memory.worker.lint import run_lint
from cadence_memory.worker.manual import ingest_file

app = typer.Typer(
    name="cadence-memory",
    help=(
        "LLM-maintained knowledge base for Claude Code (rewrite in progress — see docs/design2.md)."
    ),
    no_args_is_help=False,
)
app.add_typer(hooks_app)
app.add_typer(repos_app)
app.add_typer(worker_app)
app.command("status")(cmd_status)


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


@app.command("lint")
def cmd_lint(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Commit on the current branch instead of creating lint/<YYYY-MM-DD>.",
        ),
    ] = False,
    only: Annotated[
        str | None,
        typer.Option(
            "--only",
            help="Scope the audit to a single repo (projects/<name>/).",
        ),
    ] = None,
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Audit the master wiki for orphans, broken links, contradictions, and missing pages."""
    try:
        wiki_dir = resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())
    except WikiNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    try:
        cfg = load_config(wiki_dir / CONFIG_FILENAME)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    outcome = run_lint(
        wiki_dir=wiki_dir,
        config=cfg,
        runner=DefaultClaudeRunner(),
        apply=apply,
        only_repo=only,
    )

    switch_back: str | None = None
    if (
        not apply
        and outcome.previous_branch is not None
        and outcome.previous_branch != outcome.branch_used
    ):
        switch_back = f"switch back with `git checkout {outcome.previous_branch}`"

    if not outcome.success:
        typer.echo(f"failed: {outcome.error or 'claude run failed'}", err=True)
        if switch_back is not None:
            typer.echo(switch_back)
        raise typer.Exit(code=1)

    cost = f"${outcome.cost_usd:.2f}" if outcome.cost_usd is not None else "(n/a)"
    sha_display = outcome.wiki_commit_sha if outcome.wiki_commit_sha is not None else "no changes"
    typer.echo(
        f"ok: {sha_display} ({len(outcome.pages_touched)} pages, {cost}) "
        f"[branch={outcome.branch_used}]"
    )
    if switch_back is not None:
        typer.echo(switch_back)


_SNIPPET_TABLE_MAX = 80
_VALID_FORMATS = ("table", "json")
_VALID_BACKENDS = ("qmd", "ripgrep", "auto")


def _select_backend(name: str | None) -> SearchBackend:
    if name in (None, "auto"):
        return pick_backend()
    if name == "qmd":
        if not QmdBackend.available():
            typer.echo(
                "error: qmd not on $PATH — install via 'brew install qmd' or use --backend ripgrep",
                err=True,
            )
            raise typer.Exit(code=2)
        return QmdBackend()
    if name == "ripgrep":
        if not RipgrepBackend.available():
            typer.echo(
                "error: ripgrep not on $PATH — install it or use --backend qmd",
                err=True,
            )
            raise typer.Exit(code=2)
        return RipgrepBackend()
    raise AssertionError(f"unreachable backend name: {name!r}")


def _render_table(hits: tuple[Hit, ...], wiki_dir: Path) -> str:
    if not hits:
        return "no matches"
    headers = ("SCORE", "PATH", "SNIPPET")
    rows: list[tuple[str, str, str]] = []
    for hit in hits:
        score_cell = f"{hit.score:.2f}" if hit.score is not None else "—"
        try:
            path_cell = str(hit.path.relative_to(wiki_dir))
        except ValueError:
            path_cell = str(hit.path)
        snippet = hit.snippet.replace("\n", " ").replace("\t", " ")
        if len(snippet) > _SNIPPET_TABLE_MAX:
            snippet = snippet[:_SNIPPET_TABLE_MAX] + "…"
        rows.append((score_cell, path_cell, snippet))

    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            if len(value) > widths[i]:
                widths[i] = len(value)

    def _render(row: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[i]) for i, value in enumerate(row)).rstrip()

    lines = [_render(headers)]
    for row in rows:
        lines.append(_render(row))
    return "\n".join(lines)


def _render_json(hits: tuple[Hit, ...]) -> str:
    return json.dumps(
        [
            {
                "path": str(h.path),
                "score": h.score,
                "snippet": h.snippet,
                "backend": h.backend,
            }
            for h in hits
        ],
        indent=2,
    )


@app.command("query")
def cmd_query(
    text: Annotated[
        str,
        typer.Argument(help="Search text passed to the backend (qmd or ripgrep)."),
    ],
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Maximum number of hits to return."),
    ] = 20,
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: table or json."),
    ] = "table",
    backend: Annotated[
        str | None,
        typer.Option("--backend", help="Force a backend: qmd, ripgrep, or auto."),
    ] = None,
    wiki: Annotated[
        Path | None,
        typer.Option("--wiki", help="Wiki directory (defaults to walk-up from cwd)."),
    ] = None,
) -> None:
    """Search the master wiki via qmd (preferred) or ripgrep (fallback)."""
    if format not in _VALID_FORMATS:
        typer.echo(
            f"error: --format must be one of {', '.join(_VALID_FORMATS)} (got {format!r})",
            err=True,
        )
        raise typer.Exit(code=2)
    if backend is not None and backend not in _VALID_BACKENDS:
        typer.echo(
            f"error: --backend must be one of {', '.join(_VALID_BACKENDS)} (got {backend!r})",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        wiki_dir = resolve_wiki_dir(flag=wiki, env=dict(os.environ), cwd=Path.cwd())
    except WikiNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    try:
        chosen = _select_backend(backend)
    except NoBackendAvailableError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        hits = chosen.search(query=text, wiki_dir=wiki_dir, limit=limit)
    except SearchError as exc:
        typer.echo(f"search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if format == "json":
        typer.echo(_render_json(hits))
    else:
        typer.echo(_render_table(hits, wiki_dir))


if __name__ == "__main__":
    app()
