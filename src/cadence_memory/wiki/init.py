"""Scaffold a master-wiki repo from embedded defaults (design2 §4, §11)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import date
from enum import Enum
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

_DATE_PLACEHOLDER = "{date}"
_SEED_PREFIX = "seed/"
_HOOK_TEMPLATE_PATH = ("wiki", "git-hooks", "post-commit")


class HookInstallError(Exception):
    """Raised when the target directory has no `.git` directory."""


class InstallOutcome(Enum):
    INSTALLED = "installed"
    ALREADY_PRESENT = "already_present"
    FOREIGN_PRESENT = "foreign_present"


_FILE_MAP: tuple[tuple[str, str], ...] = (
    ("config.yaml", "config.yaml"),
    ("CLAUDE.md", "CLAUDE.md"),
    (".claude/settings.json", ".claude/settings.json"),
    (".claude/skills/wiki-researcher.md", ".claude/skills/wiki-researcher.md"),
    (".claude/skills/wiki-ingest.md", ".claude/skills/wiki-ingest.md"),
    ("gitignore", ".gitignore"),
    ("seed/index.md", "index.md"),
    ("seed/log.md", "log.md"),
    ("seed/gaps.md", "gaps.md"),
    ("raw/.gitkeep", "raw/.gitkeep"),
    ("projects/.gitkeep", "projects/.gitkeep"),
)


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    """Outcome of a `scaffold_wiki` call."""

    target: Path
    created_files: tuple[Path, ...]
    skipped_files: tuple[Path, ...]
    git_initialized: bool


def scaffold_wiki(target: Path) -> ScaffoldResult:
    """Scaffold a master-wiki repo at ``target``.

    Idempotent: existing files are skipped, not overwritten. Runs ``git init``
    when ``target`` is not already inside a git work-tree.
    """
    target.mkdir(parents=True, exist_ok=True)
    resolved_target = target.resolve()

    wiki_defaults = files("cadence_memory.defaults") / "wiki"
    today_iso = date.today().isoformat()

    created: list[Path] = []
    skipped: list[Path] = []

    for src, dst in _FILE_MAP:
        dst_abs = (resolved_target / dst).resolve()
        if dst_abs.exists():
            skipped.append(dst_abs)
            continue
        source = _traverse(wiki_defaults, src)
        data = source.read_bytes()
        if src.startswith(_SEED_PREFIX) and src.endswith(".md"):
            data = data.decode("utf-8").replace(_DATE_PLACEHOLDER, today_iso).encode("utf-8")
        dst_abs.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(dst_abs, data)
        created.append(dst_abs)

    git_initialized = _maybe_git_init(resolved_target)

    hook_path = (resolved_target / ".git" / "hooks" / "post-commit").resolve()
    if (resolved_target / ".git").is_dir():
        hook_outcome = install_post_commit_hook(resolved_target, force=False)
        if hook_outcome is InstallOutcome.INSTALLED:
            created.append(hook_path)
        else:
            skipped.append(hook_path)
    else:
        skipped.append(hook_path)

    return ScaffoldResult(
        target=resolved_target,
        created_files=tuple(created),
        skipped_files=tuple(skipped),
        git_initialized=git_initialized,
    )


def _traverse(root: Traversable, relpath: str) -> Traversable:
    current = root
    for part in relpath.split("/"):
        current = current / part
    return current


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _maybe_git_init(target: Path) -> bool:
    check = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if check.returncode == 0 and check.stdout.strip() == "true":
        return False
    subprocess.run(
        ["git", "init"],
        cwd=target,
        check=True,
        capture_output=True,
    )
    return True


def install_post_commit_hook(wiki_dir: Path, *, force: bool = False) -> InstallOutcome:
    """Install the qmd re-index post-commit hook into ``<wiki_dir>/.git/hooks/``.

    Returns INSTALLED when the file is written, ALREADY_PRESENT when the on-disk bytes
    already match the shipped template (force is a no-op in this case), and FOREIGN_PRESENT
    when a different hook exists and ``force`` is False.  Raises HookInstallError when
    ``<wiki_dir>/.git`` is absent.
    """
    resolved = wiki_dir.resolve()
    git_dir = resolved / ".git"
    if not git_dir.is_dir():
        raise HookInstallError(f"no .git directory at {resolved}")

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    defaults = files("cadence_memory.defaults")
    template = defaults
    for part in _HOOK_TEMPLATE_PATH:
        template = template / part
    template_bytes = template.read_bytes()

    if hook_path.exists():
        on_disk = hook_path.read_bytes()
        if on_disk == template_bytes:
            return InstallOutcome.ALREADY_PRESENT
        if not force:
            return InstallOutcome.FOREIGN_PRESENT

    _atomic_write_bytes(hook_path, template_bytes)
    os.chmod(hook_path, 0o755)
    return InstallOutcome.INSTALLED
