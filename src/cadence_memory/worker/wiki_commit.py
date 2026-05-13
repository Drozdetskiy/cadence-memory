"""Wiki working-tree helpers: stage, commit, revert, log failures (design2 §7)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class WikiCommitError(Exception):
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
        raise WikiCommitError(f"{argv[0:3]} failed (rc={result.returncode}): {stderr}")
    return result


def stage_and_commit(*, wiki_dir: Path, message: str) -> str | None:
    """Stage every change in `wiki_dir`, commit if anything is staged, return the new HEAD sha.

    Returns ``None`` when there is nothing to commit (clean working tree).
    Raises ``WikiCommitError`` on unexpected git failures.
    """
    _run_checked(["git", "add", "-A"], cwd=wiki_dir)
    diff_cached = _run(["git", "diff", "--cached", "--quiet"], cwd=wiki_dir)
    if diff_cached.returncode == 0:
        return None
    if diff_cached.returncode != 1:
        stderr = (diff_cached.stderr or "").strip()
        raise WikiCommitError(
            f"git diff --cached --quiet failed (rc={diff_cached.returncode}): {stderr}"
        )
    _run_checked(["git", "commit", "-m", message], cwd=wiki_dir)
    head = _run_checked(["git", "rev-parse", "HEAD"], cwd=wiki_dir)
    return head.stdout.strip()


_TOUCHED_PREFIXES: frozenset[str] = frozenset({"??", " M", "M ", "A ", "AM", "MM"})


def list_touched_paths(wiki_dir: Path) -> tuple[Path, ...]:
    """Return absolute paths to files Claude has modified or created in `wiki_dir`.

    Parses `git status --porcelain` output. Deleted files are intentionally
    omitted because they cannot be parsed as frontmatter; the orchestrator
    still picks them up via `git add -A` when it commits.
    """
    result = _run_checked(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=wiki_dir,
    )
    touched: list[Path] = []
    for line in result.stdout.splitlines():
        if len(line) < 3:
            continue
        prefix = line[:2]
        rest = line[3:]
        if prefix not in _TOUCHED_PREFIXES:
            continue
        relpath = rest.split(" -> ", 1)[-1]
        if relpath.startswith('"') and relpath.endswith('"'):
            relpath = relpath[1:-1]
        touched.append((wiki_dir / relpath).resolve())
    return tuple(touched)


def _prune_empty_parents(path: Path, *, stop_at: Path) -> None:
    parent = path.parent
    while parent != stop_at and parent.is_relative_to(stop_at):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def revert_wiki(wiki_dir: Path, *, preserve: frozenset[Path] = frozenset()) -> None:
    """Discard every Claude-authored change in `wiki_dir`.

    `preserve` carries resolved absolute paths from a pre-run
    `list_touched_paths` snapshot; any path in that set is left untouched.
    Restores tracked files to HEAD and removes untracked files / directories
    so the next ingest does not see leftover stubs.
    """
    result = _run_checked(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=wiki_dir,
    )
    tracked: list[str] = []
    untracked: list[Path] = []
    for line in result.stdout.splitlines():
        if len(line) < 3:
            continue
        prefix = line[:2]
        if prefix not in _TOUCHED_PREFIXES:
            continue
        rest = line[3:]
        relpath = rest.split(" -> ", 1)[-1]
        if relpath.startswith('"') and relpath.endswith('"'):
            relpath = relpath[1:-1]
        abs_path = (wiki_dir / relpath).resolve()
        if abs_path in preserve:
            continue
        if prefix == "??":
            untracked.append(abs_path)
        else:
            tracked.append(relpath)
    if not tracked and not untracked:
        return
    if tracked:
        _run_checked(["git", "checkout", "--", *tracked], cwd=wiki_dir)
    for path in untracked:
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
            _prune_empty_parents(path, stop_at=wiki_dir.resolve())


def append_log_failure(
    *,
    wiki_dir: Path,
    repo_name: str,
    short_sha: str,
    subject: str,
    error: str,
    today_iso: str,
) -> None:
    """Append a FAILED ingest entry to ``log.md`` and commit it."""
    log_path = wiki_dir / "log.md"
    entry = f"\n## [{today_iso}] FAILED ingest | {repo_name} {short_sha} — {subject}\n\n{error}\n"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    stage_and_commit(
        wiki_dir=wiki_dir,
        message=f"cadence-memory: ingest failure {repo_name} {short_sha}",
    )


__all__ = [
    "WikiCommitError",
    "append_log_failure",
    "list_touched_paths",
    "revert_wiki",
    "stage_and_commit",
]
