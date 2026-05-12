"""Wiki branch helpers: create-or-switch on the wiki repo (design2 §9)."""

from __future__ import annotations

import subprocess
from pathlib import Path


class BranchError(Exception):
    """Raised when a git plumbing call against the wiki dir fails unexpectedly."""


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )


def _run_checked(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = _run(argv, cwd=cwd)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise BranchError(f"{argv[0:3]} failed (rc={result.returncode}): {stderr}")
    return result


def create_or_switch_branch(wiki_dir: Path, name: str) -> str:
    """Switch the wiki working tree to branch `name`, creating it if missing.

    Returns the name of the branch that was checked out *before* the switch.
    When already on `name`, the returned previous-branch name equals `name`.

    Raises ``BranchError`` on unexpected git failures (e.g. not a git repo).
    """
    current = _run_checked(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=wiki_dir,
    ).stdout.strip()

    if current == name:
        return current

    exists = _run(["git", "rev-parse", "--verify", name], cwd=wiki_dir)
    if exists.returncode == 0:
        _run_checked(["git", "checkout", name], cwd=wiki_dir)
    else:
        _run_checked(["git", "checkout", "-b", name], cwd=wiki_dir)
    return current


__all__ = ["BranchError", "create_or_switch_branch"]
