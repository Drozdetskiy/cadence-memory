"""Resolve the store directory via --store flag, CADENCE_MEMORY_DIR env, or walk-up."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

__all__ = ["StoreNotFoundError", "resolve_store_dir"]


class StoreNotFoundError(Exception):
    """Raised when the cadence-memory store directory cannot be located.

    The message is end-user facing: it either names the directory the user
    pointed at (when --store or CADENCE_MEMORY_DIR was given but is missing
    config.yaml) or instructs the user to pass --store / set the env var
    (when walk-up from the cwd found nothing).
    """


def _looks_like_store(directory: Path) -> bool:
    return (directory / "config.yaml").is_file()


def _validate_explicit(directory: Path, *, source: str) -> Path:
    expanded = directory.expanduser()
    try:
        resolved = expanded.resolve()
    except OSError as exc:
        raise StoreNotFoundError(
            f"{source} points at {expanded}, which cannot be resolved: {exc}"
        ) from exc
    if not _looks_like_store(resolved):
        raise StoreNotFoundError(
            f"{source} points at {resolved}, which is not a cadence-memory store "
            f"(no config.yaml found)"
        )
    return resolved


def resolve_store_dir(
    *,
    flag: Path | None,
    env: Mapping[str, str],
    cwd: Path,
) -> Path:
    """Resolve the store directory using the documented precedence chain.

    Precedence:
      1. ``flag`` (the --store CLI option) — must point at an existing store.
      2. ``CADENCE_MEMORY_DIR`` env var (when non-empty) — same requirement.
      3. Walk up from ``cwd`` to the filesystem root; first directory that
         contains ``config.yaml`` wins.
      4. Otherwise, raise :class:`StoreNotFoundError`.
    """
    if flag is not None:
        return _validate_explicit(flag, source="--store")

    env_value = env.get("CADENCE_MEMORY_DIR", "")
    if env_value:
        return _validate_explicit(Path(env_value), source="CADENCE_MEMORY_DIR")

    current = cwd.expanduser().resolve()
    while True:
        if _looks_like_store(current):
            return current
        if current.parent == current:
            break
        current = current.parent

    raise StoreNotFoundError(
        "not inside a cadence-memory store; pass --store <dir> or set CADENCE_MEMORY_DIR"
    )
