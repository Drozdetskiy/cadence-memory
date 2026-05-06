"""Typer entrypoint for the cadence-memory CLI."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from collections import deque
from collections.abc import Callable, MutableMapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Annotated, Final, Literal

import typer
from ruamel.yaml.error import YAMLError

from cadence_memory import __version__, ephemeral
from cadence_memory import chat as _chat_module
from cadence_memory.config import (
    AnnotationsConfig,
    Config,
    ConfigError,
    ProjectConfig,
    load_annotations_config,
    load_config,
)
from cadence_memory.config_writer import ConfigWriter
from cadence_memory.defaults.exclude import DEFAULT_PROJECT_EXCLUDE
from cadence_memory.discover.runner import (
    DiscoverFailed,
    DiscoverInputs,
    DiscoverInvalidOutput,
    DiscoverTarget,
    DiscoverTimedOut,
    run_discover,
)
from cadence_memory.discover.scanner import scan_project
from cadence_memory.ephemeral import EphemeralAddOptions, EphemeralExists
from cadence_memory.executor.claude_executor import (
    ClaudeNotFound,
    ClaudeRunner,
    StreamingClaudeRunner,
)
from cadence_memory.formatters import Format, format_document, format_documents
from cadence_memory.reindex.diff import diff as diff_reindex
from cadence_memory.reindex.engine import ReindexError, ReindexResult
from cadence_memory.reindex.engine import reindex as run_reindex
from cadence_memory.store.sqlite_store import SqliteStore
from cadence_memory.store_locator import StoreNotFoundError, resolve_store_dir

app = typer.Typer(add_completion=False)
ephemeral_app = typer.Typer(
    add_completion=False,
    help="Manage ephemeral task notes stored under <store>/ephemeral/.",
)
app.add_typer(ephemeral_app, name="ephemeral")
projects_app = typer.Typer(
    add_completion=False,
    help="Manage entries in config.yaml's projects: list.",
)
app.add_typer(projects_app, name="projects")

_PROJECT_NAME_RE: Final = re.compile(r"^[a-z0-9_-]+$")

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


def _chat_install_hint() -> str:
    py = shlex.quote(sys.executable)
    return (
        "note: install the skill once with:\n"
        "  mkdir -p ~/.claude/skills/cadence-memory\n"
        f'  cp $({py} -c "from importlib.resources import files; '
        "print(files('cadence_memory.defaults.skills') / 'cadence-memory.md')\") "
        "~/.claude/skills/cadence-memory/SKILL.md"
    )


_chat_spawn: Callable[[Sequence[str], MutableMapping[str, str]], int] = _chat_module._default_spawn
_chat_which: Callable[[str], str | None] = shutil.which


def _chat_run(
    *,
    store_dir: Path,
    extra_args: Sequence[str],
    env: MutableMapping[str, str],
) -> int:
    return _chat_module.run_chat(
        store_dir=store_dir,
        extra_args=extra_args,
        env=env,
        spawn=_chat_spawn,
        which=_chat_which,
    )


@app.command()
def chat(
    ctx: typer.Context,
    args: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[-- claude args]",
            help="Optional trailing arguments forwarded verbatim to `claude`.",
        ),
    ] = None,
) -> None:
    """Spawn an interactive Claude session pointed at the cadence-memory store."""
    flag = ctx.obj.get("store") if ctx.obj else None
    try:
        store_dir = resolve_store_dir(flag=flag, env=os.environ, cwd=Path.cwd())
    except StoreNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"chat: store={store_dir}")

    skill_path = Path.home() / ".claude" / "skills" / "cadence-memory" / "SKILL.md"
    if not skill_path.exists():
        typer.echo("")
        typer.echo(_chat_install_hint())

    rc = _chat_run(
        store_dir=store_dir,
        extra_args=tuple(args or ()),
        env=os.environ.copy(),
    )
    raise typer.Exit(code=rc)


def _default_discover_runner_factory(idle_timeout: float) -> ClaudeRunner:
    return StreamingClaudeRunner(
        idle_timeout=idle_timeout,
        output_handler=lambda chunk: typer.echo(chunk, nl=False, err=True),
        activity_handler=lambda tool: typer.echo(f"  -> {tool}", err=True),
    )


def _default_discover_run(inputs: DiscoverInputs, runner: ClaudeRunner) -> None:
    run_discover(inputs, runner=runner)


def _default_git_dirty_check(store_dir: Path, filename: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", filename],
            cwd=str(store_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


_discover_runner_factory: Callable[[float], ClaudeRunner] = _default_discover_runner_factory
_discover_run_func: Callable[[DiscoverInputs, ClaudeRunner], None] = _default_discover_run
_git_dirty_check: Callable[[Path, str], bool] = _default_git_dirty_check


@app.command()
def discover(
    ctx: typer.Context,
    project: Annotated[
        list[str] | None,
        typer.Option(
            "--project",
            help="Limit discovery to the named project. Repeat to target multiple projects.",
        ),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "Overwrite annotations-config.yaml directly. "
                "Without this flag, output goes to annotations-config.yaml.proposed for review."
            ),
        ),
    ] = False,
    idle_timeout: Annotated[
        float,
        typer.Option(
            "--idle-timeout",
            help="Seconds without Claude output before aborting; 0 disables the watchdog.",
        ),
    ] = 300.0,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help=(
                "Bypass the discover cache for reads (force re-running Claude on every file). "
                "Cache is still populated on success. Use this if kind_rules or the prompt "
                "have changed."
            ),
        ),
    ] = False,
) -> None:
    """Run Claude to populate annotations-config.yaml from project markdown files."""
    flag = ctx.obj.get("store") if ctx.obj else None
    try:
        store_dir = resolve_store_dir(flag=flag, env=os.environ, cwd=Path.cwd())
    except StoreNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        cfg = load_config(store_dir / "config.yaml")
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    selected_names = list(project or [])
    if selected_names:
        known_by_name = {p.name: p for p in cfg.projects}
        for name in selected_names:
            if name not in known_by_name:
                typer.echo(f"error: unknown project: {name}", err=True)
                known = ", ".join(p.name for p in cfg.projects)
                typer.echo(f"known projects: {known}", err=True)
                raise typer.Exit(code=1)
        selected_projects = tuple(known_by_name[name] for name in selected_names)
    else:
        selected_projects = cfg.projects

    if not selected_projects:
        typer.echo("error: no projects configured", err=True)
        raise typer.Exit(code=1)

    targets = tuple(
        DiscoverTarget(project=p, files=tuple(scan_project(p))) for p in selected_projects
    )

    if apply:
        output_path = store_dir / "annotations-config.yaml"
        if _git_dirty_check(store_dir, "annotations-config.yaml"):
            typer.echo(
                (
                    "error: annotations-config.yaml has uncommitted changes; "
                    "commit or stash before re-running with --apply"
                ),
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        output_path = store_dir / "annotations-config.yaml.proposed"
        if output_path.exists():
            typer.echo(
                "warn: overwriting existing annotations-config.yaml.proposed",
                err=True,
            )

    n_projects = len(targets)
    n_files = sum(len(t.files) for t in targets)
    typer.echo(
        f"discover: {n_projects} project(s), {n_files} file(s) -> {output_path}",
        err=True,
    )

    runner = _discover_runner_factory(idle_timeout)

    try:
        store = SqliteStore(store_dir / "index.sqlite")
    except (OSError, sqlite3.Error) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    inputs = DiscoverInputs(
        store_dir=store_dir,
        targets=targets,
        output_path=output_path,
        store=store,
        cache_read=not no_cache,
    )

    try:
        try:
            _discover_run_func(inputs, runner)
        except DiscoverTimedOut:
            typer.echo("discover failed: idle timeout", err=True)
            raise typer.Exit(code=4) from None
        except DiscoverFailed as exc:
            typer.echo(f"discover failed: claude exited with code {exc.exit_code}", err=True)
            raise typer.Exit(code=2) from None
        except DiscoverInvalidOutput as exc:
            typer.echo(
                f"discover failed: invalid output at {exc.path}: {exc.message}",
                err=True,
            )
            raise typer.Exit(code=3) from None
        except ClaudeNotFound:
            typer.echo("discover failed: claude not found on PATH", err=True)
            raise typer.Exit(code=127) from None
    finally:
        store.close()

    typer.echo(f"wrote: {output_path}")
    if not apply:
        typer.echo("next: review the diff, then re-run with --apply")


def _parse_tags(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(tag.strip() for tag in raw.split(",") if tag.strip())


@ephemeral_app.command("add")
def ephemeral_add(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Argument(
            help="Source markdown file to copy or symlink. Omit when --inline - is used.",
        ),
    ] = None,
    eph_id: Annotated[
        str | None,
        typer.Option(
            "--id",
            help="Explicit ephemeral name (slug under 'eph:'). Required for --inline -.",
        ),
    ] = None,
    kind: Annotated[
        str,
        typer.Option("--kind", help="Document kind annotation."),
    ] = "task",
    title: Annotated[
        str | None,
        typer.Option("--title", help="Override the document title."),
    ] = None,
    tags: Annotated[
        str | None,
        typer.Option("--tags", help="Comma-separated list of tags."),
    ] = None,
    symlink: Annotated[
        bool,
        typer.Option("--symlink/--no-symlink", help="Symlink the source instead of copying it."),
    ] = False,
    inline: Annotated[
        str | None,
        typer.Option(
            "--inline",
            help="Read the document body from stdin. Only legal value: '-'.",
        ),
    ] = None,
) -> None:
    """Add an ephemeral document by copying, symlinking, or reading stdin."""
    if inline is not None and inline != "-":
        raise typer.BadParameter("only '-' is supported (read from stdin)", param_hint="--inline")

    inline_text: str | None = None
    source_path: Path | None = None
    if inline == "-":
        if eph_id is None:
            typer.echo("error: --inline - requires --id", err=True)
            raise typer.Exit(code=1)
        if path is not None:
            typer.echo("error: cannot combine --inline - with a positional path", err=True)
            raise typer.Exit(code=1)
        inline_text = sys.stdin.read()
    else:
        if path is None:
            typer.echo(
                "error: a source file argument is required unless --inline - is used",
                err=True,
            )
            raise typer.Exit(code=1)
        source_path = path

    opts = EphemeralAddOptions(
        source=source_path,
        eph_id=eph_id,
        kind=kind,
        title=title,
        tags=_parse_tags(tags),
        use_symlink=symlink,
        inline_text=inline_text,
    )

    store_dir, _, _, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            doc = ephemeral.add(opts, store=store, store_dir=store_dir)
        except EphemeralExists as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except (OSError, sqlite3.Error) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(doc.id)


@ephemeral_app.command("list")
def ephemeral_list(
    ctx: typer.Context,
    format: Annotated[
        Literal["json", "table"] | None,
        typer.Option(
            "--format",
            help="Output format. Defaults to table when stdout is a TTY, json otherwise.",
        ),
    ] = None,
) -> None:
    """List ephemeral documents."""
    _, _, _, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            docs = ephemeral.list_(store)
        except sqlite3.Error as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    fmt = _resolve_format(format)
    typer.echo(format_documents(docs, format=fmt, include_body=False))


@ephemeral_app.command("remove")
def ephemeral_remove(
    ctx: typer.Context,
    id: Annotated[
        str,
        typer.Argument(help="Ephemeral document id (e.g. 'eph:mynote')."),
    ],
) -> None:
    """Remove a single ephemeral document by id."""
    store_dir, _, _, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            ephemeral.remove(id, store=store, store_dir=store_dir)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except (OSError, sqlite3.Error) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(f"removed: {id}")


@ephemeral_app.command("clear")
def ephemeral_clear(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the interactive confirmation prompt."),
    ] = False,
) -> None:
    """Remove all ephemeral documents (rows and on-disk files)."""
    if not yes and not typer.confirm("Remove all ephemeral documents?", default=False):
        typer.echo("aborted")
        raise typer.Exit(code=1)

    store_dir, _, _, store = _load_store_context(ctx, Path.cwd())
    try:
        try:
            count = ephemeral.clear(store=store, store_dir=store_dir)
        except (OSError, sqlite3.Error) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(f"cleared: {count} document(s)")


def _resolve_store_dir_or_exit(ctx: typer.Context) -> Path:
    flag = ctx.obj.get("store") if ctx.obj else None
    try:
        return resolve_store_dir(flag=flag, env=os.environ, cwd=Path.cwd())
    except StoreNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _open_config_writer_or_exit(store_dir: Path) -> ConfigWriter:
    try:
        return ConfigWriter(store_dir / "config.yaml")
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except YAMLError as exc:
        typer.echo(f"error: failed to parse config.yaml: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _format_projects_table(projects: list[ProjectConfig]) -> str:
    columns: tuple[str, ...] = ("name", "path", "excludes")
    rows: list[tuple[str, ...]] = [columns]
    for project in projects:
        rows.append((project.name, str(project.path), str(len(project.exclude))))
    widths = [max(len(row[col_idx]) for row in rows) for col_idx in range(len(columns))]
    lines = [
        "  ".join(value.ljust(widths[col_idx]) for col_idx, value in enumerate(row)).rstrip()
        for row in rows
    ]
    return "\n".join(lines)


def _format_projects_json(projects: list[ProjectConfig]) -> str:
    payload = [
        {"name": project.name, "path": str(project.path), "excludes": len(project.exclude)}
        for project in projects
    ]
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)


@projects_app.command("add")
def projects_add(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Argument(help="Project name. Must match ^[a-z0-9_-]+$."),
    ],
    path: Annotated[
        Path,
        typer.Argument(help="Filesystem path to the project root."),
    ],
    no_default_exclude: Annotated[
        bool,
        typer.Option(
            "--no-default-exclude",
            help="Write `exclude: []` instead of the default Python exclude globs.",
        ),
    ] = False,
) -> None:
    """Add a project entry to config.yaml."""
    if not _PROJECT_NAME_RE.match(name):
        typer.echo(f"error: invalid project name: {name}", err=True)
        raise typer.Exit(code=1)

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        typer.echo(f"error: path is not a directory: {resolved}", err=True)
        raise typer.Exit(code=1)

    store_dir = _resolve_store_dir_or_exit(ctx)
    writer = _open_config_writer_or_exit(store_dir)

    exclude: Sequence[str] | None = () if no_default_exclude else None
    try:
        result = writer.add_project(name=name, path=resolved, exclude=exclude)
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not result.added:
        typer.echo(f"error: project already exists: {name}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"added project: {name} -> {resolved}")


@projects_app.command("list")
def projects_list(
    ctx: typer.Context,
    format: Annotated[
        Literal["json", "table"] | None,
        typer.Option(
            "--format",
            help="Output format. Defaults to table when stdout is a TTY, json otherwise.",
        ),
    ] = None,
) -> None:
    """List projects registered in config.yaml."""
    store_dir = _resolve_store_dir_or_exit(ctx)
    try:
        cfg = load_config(store_dir / "config.yaml")
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    projects = list(cfg.projects)
    if not projects:
        typer.echo("(no projects registered)", err=True)
        return

    fmt = _resolve_format(format)
    if fmt == "json":
        typer.echo(_format_projects_json(projects))
    else:
        typer.echo(_format_projects_table(projects))


@projects_app.command("remove")
def projects_remove(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Argument(help="Project name to remove."),
    ],
) -> None:
    """Remove a project entry from config.yaml."""
    store_dir = _resolve_store_dir_or_exit(ctx)
    writer = _open_config_writer_or_exit(store_dir)

    try:
        removed = writer.remove_project(name)
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not removed:
        typer.echo(f"error: project not found: {name}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"removed project: {name}")


def _slugify_project_name(raw: str) -> str:
    lowered = raw.lower()
    transformed = re.sub(r"[^a-z0-9_-]", "_", lowered)
    collapsed = re.sub(r"_+", "_", transformed)
    stripped = collapsed.strip("_-")
    return stripped if stripped else "project"


def _exclude_basename_patterns() -> tuple[str, ...]:
    patterns: list[str] = []
    for raw in DEFAULT_PROJECT_EXCLUDE:
        parts = [part for part in raw.split("/") if part and part != "**"]
        if parts:
            patterns.append(parts[-1])
    return tuple(patterns)


def _is_excluded_basename(basename: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(basename, pattern) for pattern in patterns)


def _autodetect_projects(root: Path, depth: int) -> list[Path]:
    excluded = _exclude_basename_patterns()
    found: list[Path] = []
    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    while queue:
        current, current_depth = queue.popleft()
        if (current / ".git").exists():
            found.append(current)
            continue
        if current_depth >= depth:
            continue
        try:
            children = sorted(child for child in current.iterdir() if child.is_dir())
        except OSError:
            continue
        for child in children:
            if _is_excluded_basename(child.name, excluded):
                continue
            queue.append((child, current_depth + 1))
    return found


@projects_app.command("autodetect")
def projects_autodetect(
    ctx: typer.Context,
    root: Annotated[
        Path,
        typer.Option("--root", help="Root directory to scan for git repos."),
    ] = Path("."),
    depth: Annotated[
        int,
        typer.Option("--depth", help="Maximum directory depth to scan from --root."),
    ] = 3,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Add discovered repos to config.yaml. Without it, only print candidates.",
        ),
    ] = False,
) -> None:
    """Discover git repos under --root and (optionally) register them as projects."""
    if depth < 0:
        typer.echo("error: --depth must be >= 0", err=True)
        raise typer.Exit(code=1)

    resolved_root = Path(root).expanduser().resolve()
    if not resolved_root.is_dir():
        typer.echo(f"error: --root is not a directory: {resolved_root}", err=True)
        raise typer.Exit(code=1)

    candidates = _autodetect_projects(resolved_root, depth)
    pairs = [(_slugify_project_name(path.name), path) for path in candidates]

    if not apply:
        for slug, path in pairs:
            typer.echo(f"+ {slug} -> {path}")
        typer.echo(
            f"autodetect: {len(pairs)} candidate(s); rerun with --apply to write",
            err=True,
        )
        return

    store_dir = _resolve_store_dir_or_exit(ctx)
    writer = _open_config_writer_or_exit(store_dir)

    added = 0
    for slug, path in pairs:
        try:
            result = writer.add_project(name=slug, path=path, exclude=None)
        except OSError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        if not result.added:
            typer.echo(f"warn: project already exists: {slug}", err=True)
            continue
        typer.echo(f"added project: {slug} -> {path}")
        added += 1

    typer.echo(f"autodetect: added {added} of {len(pairs)}", err=True)
