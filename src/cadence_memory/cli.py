"""Typer entrypoint for the cadence-memory CLI."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

import typer

from cadence_memory import __version__
from cadence_memory.config import (
    AnnotationsConfig,
    Config,
    ConfigError,
    load_annotations_config,
    load_config,
)
from cadence_memory.formatters import Format, format_document, format_documents
from cadence_memory.reindex.diff import diff as diff_reindex
from cadence_memory.reindex.engine import ReindexError, ReindexResult
from cadence_memory.reindex.engine import reindex as run_reindex
from cadence_memory.store.sqlite_store import SqliteStore
from cadence_memory.store_locator import StoreNotFoundError, resolve_store_dir

app = typer.Typer(add_completion=False)

_DEFAULT_FILES: tuple[tuple[str, str], ...] = (
    ("config.yaml", "config.yaml"),
    ("annotations-config.yaml", "annotations-config.yaml"),
    ("gitignore", ".gitignore"),
)

_NEXT_STEPS = (
    "next steps:\n"
    "  1. edit config.yaml to register your projects\n"
    "  2. run `cadence-memory discover` (once available) or hand-edit annotations-config.yaml\n"
    "  3. run `cadence-memory reindex` to build the index"
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cadence-memory {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    store: Annotated[
        Path | None,
        typer.Option(
            "--store",
            help=(
                "Path to the cadence-memory store directory. "
                "Overrides CADENCE_MEMORY_DIR and walk-up detection."
            ),
        ),
    ] = None,
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
    ctx.ensure_object(dict)
    ctx.obj["store"] = store


def _copy_default(name: str, destination: Path) -> None:
    text = resources.files("cadence_memory.defaults").joinpath(name).read_text(encoding="utf-8")
    destination.write_text(text, encoding="utf-8")


def _run_git_init(directory: Path) -> None:
    if (directory / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "init", "-q"],
            cwd=str(directory),
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        typer.echo(f"warning: git init failed: {exc}", err=True)


@app.command()
def init(
    directory: Annotated[
        Path,
        typer.Argument(help="Directory to bootstrap as a cadence-memory store."),
    ] = Path("."),
) -> None:
    """Bootstrap a new cadence-memory store directory."""
    target = directory.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    existing = [
        target / dest_name for _, dest_name in _DEFAULT_FILES if (target / dest_name).exists()
    ]
    if existing:
        names = ", ".join(str(p) for p in existing)
        typer.echo(
            f"error: {names} already exists; refusing to overwrite",
            err=True,
        )
        raise typer.Exit(code=1)

    created: list[str] = []
    for source_name, dest_name in _DEFAULT_FILES:
        dest = target / dest_name
        _copy_default(source_name, dest)
        created.append(dest_name)

    ephemeral_dir = target / "ephemeral"
    ephemeral_dir.mkdir(exist_ok=True)
    gitkeep = ephemeral_dir / ".gitkeep"
    gitkeep.write_text("", encoding="utf-8")
    created.append("ephemeral/.gitkeep")

    db_path = target / "index.sqlite"
    try:
        store = SqliteStore(db_path)
        store.close()
    except (OSError, sqlite3.Error) as exc:
        typer.echo(f"error: could not create {db_path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    created.append("index.sqlite")

    _run_git_init(target)

    for name in created:
        typer.echo(f"created: {name}")
    typer.echo("")
    typer.echo(_NEXT_STEPS)


def _load_store_context(
    ctx: typer.Context, cwd: Path
) -> tuple[Path, Config, AnnotationsConfig, SqliteStore]:
    flag = ctx.obj.get("store") if ctx.obj else None
    try:
        store_dir = resolve_store_dir(flag=flag, env=os.environ, cwd=cwd)
    except StoreNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        cfg = load_config(store_dir / "config.yaml")
        annotations = load_annotations_config(store_dir / "annotations-config.yaml", config=cfg)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        store = SqliteStore(store_dir / "index.sqlite")
    except (OSError, sqlite3.Error) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    return store_dir, cfg, annotations, store


def _format_summary(result: ReindexResult, *, dry_run: bool) -> str:
    line = (
        f"inserted: {len(result.inserted)}, "
        f"content-updated: {len(result.updated_content)}, "
        f"metadata-updated: {len(result.updated_metadata_only)}, "
        f"deleted: {len(result.deleted)}, "
        f"skipped: {len(result.skipped_optional_missing)}"
    )
    if dry_run:
        line += " (dry-run)"
    return line


def _print_verbose(result: ReindexResult) -> None:
    buckets: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("inserted", result.inserted),
        ("content-updated", result.updated_content),
        ("metadata-updated", result.updated_metadata_only),
        ("deleted", result.deleted),
        ("skipped", result.skipped_optional_missing),
    )
    for label, ids in buckets:
        if not ids:
            continue
        typer.echo(f"{label}:")
        for doc_id in ids:
            typer.echo(f"  {doc_id}")


@app.command()
def reindex(
    ctx: typer.Context,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="List affected document ids per bucket."),
    ] = False,
) -> None:
    """Rebuild the SQLite index from config.yaml and annotations-config.yaml."""
    store_dir, cfg, annotations, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            result = run_reindex(
                config=cfg,
                annotations=annotations,
                store=store,
                store_dir=store_dir,
            )
        except (ReindexError, sqlite3.Error) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    if verbose:
        _print_verbose(result)
    typer.echo(_format_summary(result, dry_run=False))


def _resolve_format(flag: Format | None) -> Format:
    if flag is not None:
        return flag
    return "table" if sys.stdout.isatty() else "json"


@app.command(name="list")
def list_(
    ctx: typer.Context,
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Filter by document kind."),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option("--project", help="Filter by project name."),
    ] = None,
    source_type: Annotated[
        Literal["project", "global", "ephemeral"] | None,
        typer.Option(
            "--source-type",
            help="Filter by source_type (project, global, ephemeral).",
        ),
    ] = None,
    format: Annotated[
        Literal["json", "table"] | None,
        typer.Option(
            "--format",
            help="Output format. Defaults to table when stdout is a TTY, json otherwise.",
        ),
    ] = None,
) -> None:
    """List indexed documents, optionally filtered by kind/project/source-type."""
    _, _, _, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            docs = store.list(kind=kind, project=project, source_type=source_type)
        except sqlite3.Error as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    fmt = _resolve_format(format)
    typer.echo(format_documents(docs, format=fmt, include_body=False))


@app.command()
def query(
    ctx: typer.Context,
    text: Annotated[
        str,
        typer.Argument(
            help=(
                "FTS5 MATCH expression. Passed verbatim to SQLite FTS5 — "
                "supports phrase, prefix (foo*), and boolean operators "
                "(AND/OR/NOT). Quoting/escaping is the caller's responsibility."
            ),
        ),
    ],
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Filter by document kind."),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option("--project", help="Filter by project name."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum number of results."),
    ] = 20,
    format: Annotated[
        Literal["json", "table"] | None,
        typer.Option(
            "--format",
            help="Output format. Defaults to table when stdout is a TTY, json otherwise.",
        ),
    ] = None,
) -> None:
    """Full-text search over indexed documents via SQLite FTS5."""
    if limit < 1:
        typer.echo("error: --limit must be >= 1", err=True)
        raise typer.Exit(code=1)
    _, _, _, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            docs = store.query(text, kind=kind, project=project, limit=limit)
        except sqlite3.Error as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    fmt = _resolve_format(format)
    typer.echo(format_documents(docs, format=fmt, include_body=False))


@app.command()
def get(
    ctx: typer.Context,
    id: Annotated[
        str,
        typer.Argument(help="Document id (e.g. project:path/to/file.md)."),
    ],
) -> None:
    """Print the raw markdown body of a document for piping."""
    _, _, _, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            doc = store.get(id)
        except sqlite3.Error as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    if doc is None:
        typer.echo(f"error: document not found: {id}", err=True)
        raise typer.Exit(code=1)

    body = doc.body if doc.body.endswith("\n") else doc.body + "\n"
    typer.echo(body, nl=False)


@app.command()
def show(
    ctx: typer.Context,
    id: Annotated[
        str,
        typer.Argument(help="Document id (e.g. project:path/to/file.md)."),
    ],
    format: Annotated[
        Literal["json", "table"] | None,
        typer.Option(
            "--format",
            help="Output format. Defaults to table when stdout is a TTY, json otherwise.",
        ),
    ] = None,
) -> None:
    """Render a document with metadata and body."""
    _, _, _, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            doc = store.get(id)
        except sqlite3.Error as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    if doc is None:
        typer.echo(f"error: document not found: {id}", err=True)
        raise typer.Exit(code=1)

    fmt = _resolve_format(format)
    typer.echo(format_document(doc, format=fmt, include_body=True))


@app.command()
def status(ctx: typer.Context) -> None:
    """Dry-run reindex: report what would change without writing to the store."""
    store_dir, cfg, annotations, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            result = diff_reindex(
                config=cfg,
                annotations=annotations,
                store=store,
                store_dir=store_dir,
            )
        except (ReindexError, sqlite3.Error) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(_format_summary(result, dry_run=True))
