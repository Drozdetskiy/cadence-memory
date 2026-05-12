"""Round-trip YAML helpers for editing config.yaml without losing comments (design2 §11)."""

from __future__ import annotations

import dataclasses
import io
import json
import os
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from cadence_memory.config.errors import ConfigError
from cadence_memory.config.loader import SLUG_RE, load_config, parse_config

REPO_KEY_ORDER: tuple[str, ...] = (
    "name",
    "url",
    "branch",
    "start_commit",
    "model",
    "budget_usd",
    "exclude",
)


def _new_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def load_yaml_roundtrip(path: Path) -> Any:
    """Read a YAML file preserving comments, quoting, and key ordering."""
    text = path.read_text(encoding="utf-8")
    return _new_yaml().load(text)


def dump_yaml_roundtrip(path: Path, doc: Any) -> None:
    """Atomically write a round-trip YAML document.

    Mirrors the worker/state.py recipe: write to a sibling `.tmp` file, fsync
    best-effort, then `os.replace` over the target.
    """
    buffer = io.StringIO()
    _new_yaml().dump(doc, buffer)
    payload = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    try:
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass
    os.replace(tmp, path)


def _validate_pending_doc(doc: Any, path: Path) -> None:
    """Strict-validate the in-memory round-trip doc before overwriting disk.

    Why: a failing post-write load_config would leave a half-written, invalid
    config.yaml on disk; validating first means a rejected add_repo call leaves
    the user's existing config untouched.
    """
    buffer = io.StringIO()
    _new_yaml().dump(doc, buffer)
    try:
        raw = yaml.safe_load(buffer.getvalue())
    except yaml.YAMLError as exc:
        raise ConfigError(path, f"invalid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(path, "top-level must be a mapping")
    parse_config(cast(dict[str, object], raw), path=path)


def _build_repo_entry(
    *,
    name: str,
    url: str,
    branch: str,
    start_commit: str | None,
    model: str | None,
    budget_usd: float | None,
) -> Any:
    fields: dict[str, Any] = {
        "name": name,
        "url": url,
        "branch": branch,
        "start_commit": start_commit,
        "model": model,
        "budget_usd": budget_usd,
    }
    entry = CommentedMap()
    for key in REPO_KEY_ORDER:
        if key not in fields:
            continue
        value = fields[key]
        if value is None:
            continue
        entry[key] = value
    return entry


def add_repo(
    config_path: Path,
    *,
    name: str,
    url: str,
    branch: str = "main",
    start_commit: str | None = None,
    model: str | None = None,
    budget_usd: float | None = None,
) -> None:
    """Append a new repo entry to `config.yaml` in-place.

    Validates the existing file via the strict loader first, rejects bad slugs
    and duplicate names, then writes via the round-trip helpers so unrelated
    keys, comments, and quoting survive untouched. After the write a second
    strict load verifies invariants.
    """
    config = load_config(config_path)
    if not SLUG_RE.match(name):
        raise ConfigError(config_path, f"name: not a valid slug ({name!r})")
    if any(r.name == name for r in config.repos):
        raise ConfigError(config_path, f"repo {name!r} already exists")

    doc = load_yaml_roundtrip(config_path)
    if doc is None:
        doc = CommentedMap()
    repos = doc.get("repos")
    if repos is None:
        repos = CommentedSeq()
        doc["repos"] = repos
    entry = _build_repo_entry(
        name=name,
        url=url,
        branch=branch,
        start_commit=start_commit,
        model=model,
        budget_usd=budget_usd,
    )
    repos.append(entry)
    _validate_pending_doc(doc, config_path)
    dump_yaml_roundtrip(config_path, doc)
    load_config(config_path)


def remove_repo(config_path: Path, *, name: str) -> None:
    """Remove the repo named `name` from `config.yaml` in-place."""
    load_config(config_path)
    doc = load_yaml_roundtrip(config_path)
    repos = doc.get("repos") if isinstance(doc, CommentedMap) else None
    if repos is None:
        raise ConfigError(config_path, f"no repo named {name!r}")
    index: int | None = None
    for i, entry in enumerate(repos):
        if isinstance(entry, dict) and entry.get("name") == name:
            index = i
            break
    if index is None:
        raise ConfigError(config_path, f"no repo named {name!r}")
    del repos[index]
    dump_yaml_roundtrip(config_path, doc)
    load_config(config_path)


def list_repos(config_path: Path, *, format: Literal["table", "json"] = "table") -> str:
    """Render the current `repos[]` as a table or JSON string."""
    config = load_config(config_path)
    if format == "json":
        return json.dumps([dataclasses.asdict(r) for r in config.repos], indent=2)
    if format == "table":
        return _format_table(config.repos)
    raise ValueError(f"unknown format {format!r}")


def _format_table(repos: Any) -> str:
    headers = ("NAME", "URL", "BRANCH", "MODEL", "START")
    rows: list[tuple[str, str, str, str, str]] = []
    for repo in repos:
        rows.append(
            (
                repo.name,
                repo.url,
                repo.branch,
                "<default>" if repo.model is None else repo.model,
                "-" if repo.start_commit is None else repo.start_commit,
            )
        )
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


__all__ = [
    "REPO_KEY_ORDER",
    "add_repo",
    "dump_yaml_roundtrip",
    "list_repos",
    "load_yaml_roundtrip",
    "remove_repo",
]
