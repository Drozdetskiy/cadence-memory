"""Resolve the wiki directory from CLI flag, env var, or walk-up search."""

from __future__ import annotations

from pathlib import Path

CONFIG_FILENAME = "config.yaml"
ENV_VAR = "CADENCE_MEMORY_WIKI"


class WikiNotFoundError(Exception):
    """Raised when no wiki directory containing config.yaml can be located."""


def _walk_up(start: Path) -> Path | None:
    current = start.resolve()
    if (current / CONFIG_FILENAME).is_file():
        return current
    for parent in current.parents:
        if (parent / CONFIG_FILENAME).is_file():
            return parent
    return None


def _validated_explicit(path: Path, source: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir() or not (resolved / CONFIG_FILENAME).is_file():
        raise WikiNotFoundError(f"{source} {path!s} does not contain {CONFIG_FILENAME}")
    return resolved


def resolve_wiki_dir(
    *,
    flag: Path | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Locate the wiki directory using the precedence chain.

    Precedence:
      1. ``flag`` (e.g. ``--wiki`` from the CLI) — strict, no fallback.
      2. ``env[CADENCE_MEMORY_WIKI]`` if non-empty — strict, no fallback.
      3. Walk up from ``cwd`` (or ``Path.cwd()``) looking for ``config.yaml``.

    Returns the resolved (canonical) directory path. Raises
    :class:`WikiNotFoundError` if no valid wiki can be located.
    """
    if flag is not None:
        return _validated_explicit(flag, source="--wiki")

    if env is not None:
        env_value = env.get(ENV_VAR, "")
        if env_value:
            return _validated_explicit(Path(env_value), source=f"${ENV_VAR}")

    start = cwd if cwd is not None else Path.cwd()
    found = _walk_up(start)
    if found is None:
        raise WikiNotFoundError(f"no {CONFIG_FILENAME} found in {start!s} or any parent directory")
    return found
