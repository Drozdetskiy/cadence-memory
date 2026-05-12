"""Local git cache: clone & incrementally fetch source repos (design2 §6.1 step 1)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cadence_memory.git.errors import GitError, HistoryRewrittenError


@dataclass(frozen=True, slots=True)
class CloneResult:
    path: Path
    head_sha: str
    was_initial_clone: bool


@dataclass(frozen=True, slots=True)
class CommitInfo:
    sha: str
    short_sha: str
    subject: str
    body: str
    author_date_iso: str
    parents: tuple[str, ...]


class GitCache(Protocol):
    def ensure(self, *, name: str, url: str, branch: str) -> CloneResult: ...

    def head(self, *, name: str, branch: str) -> str: ...

    def head_local(self, *, name: str, branch: str) -> str | None:
        """Return the locally-known SHA of `origin/<branch>` without fetching.

        Returns `None` if the repo is not yet cloned or the ref does not exist
        locally. Never performs network I/O.
        """
        ...

    def show_commit(self, *, name: str, sha: str) -> CommitInfo: ...

    def diff(self, *, name: str, sha: str) -> str: ...

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]: ...

    def list_commits(
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]: ...


class DefaultGitCache:
    def __init__(self, *, root: Path) -> None:
        self._root = root

    def _run(self, argv: list[str], *, cwd: Path | None = None) -> str:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise GitError(f"{argv[0:2]} failed: {stderr}")
        return result.stdout

    def head(self, *, name: str, branch: str) -> str:
        return self._run(
            ["git", "rev-parse", f"origin/{branch}"],
            cwd=self._root / name,
        ).strip()

    def head_local(self, *, name: str, branch: str) -> str | None:
        path = self._root / name
        if not path.exists():
            return None
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"origin/{branch}"],
            cwd=path,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        return sha or None

    def ensure(self, *, name: str, url: str, branch: str) -> CloneResult:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / name
        if not path.exists():
            self._run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-tags",
                    "--branch",
                    branch,
                    url,
                    str(path),
                ]
            )
            was_initial = True
        else:
            existing_url = self._run(
                ["git", "remote", "get-url", "origin"],
                cwd=path,
            ).strip()
            if existing_url != url.strip():
                raise GitError(
                    f"git cache for {name!r} has remote {existing_url!r}, "
                    f"but config requests {url!r}"
                )
            self._run(
                ["git", "fetch", "--no-tags", "origin", branch],
                cwd=path,
            )
            was_initial = False
        head_sha = self.head(name=name, branch=branch)
        return CloneResult(path=path, head_sha=head_sha, was_initial_clone=was_initial)

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:
        fmt = "--format=%H%x00%h%x00%s%x00%P%x00%aI%x00%b%x1e"
        output = self._run(
            ["git", "log", "-1", fmt, sha],
            cwd=self._root / name,
        )
        records = _parse_log_records(output)
        if len(records) != 1:
            raise GitError(f"expected exactly one commit record for {sha}, got {len(records)}")
        return records[0]

    def diff(self, *, name: str, sha: str) -> str:
        return self._run(
            ["git", "show", "--format=", "--no-color", sha],
            cwd=self._root / name,
        )

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:
        output = self._run(
            ["git", "show", "--name-only", "--format=", sha],
            cwd=self._root / name,
        )
        return tuple(line for line in output.splitlines() if line)

    def list_commits(
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]:
        path = self._root / name
        if since_sha is not None:
            ancestry = subprocess.run(
                [
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    since_sha,
                    f"origin/{branch}",
                ],
                cwd=path,
                check=False,
                text=True,
                capture_output=True,
            )
            if ancestry.returncode != 0:
                raise HistoryRewrittenError(
                    f"{since_sha} not an ancestor of origin/{branch} in {name}"
                )
            rev_range = f"{since_sha}..origin/{branch}"
        else:
            rev_range = f"origin/{branch}"

        fmt = "--format=%H%x00%h%x00%s%x00%P%x00%aI%x00%b%x1e"
        argv = ["git", "log", "--topo-order"]
        if reverse:
            argv.append("--reverse")
        argv.extend([fmt, rev_range])
        output = self._run(argv, cwd=path)
        return _parse_log_records(output)


def _parse_log_records(output: str) -> tuple[CommitInfo, ...]:
    records: list[CommitInfo] = []
    for raw in output.split("\x1e"):
        record = raw.lstrip("\r\n")
        if not record:
            continue
        fields = record.split("\x00")
        if len(fields) != 6:
            raise GitError(f"unexpected git log record: expected 6 fields, got {len(fields)}")
        sha, short_sha, subject, parents_raw, author_date_iso, body = fields
        parents = tuple(parents_raw.split()) if parents_raw else ()
        records.append(
            CommitInfo(
                sha=sha,
                short_sha=short_sha,
                subject=subject,
                body=body.rstrip(),
                author_date_iso=author_date_iso,
                parents=parents,
            )
        )
    return tuple(records)


__all__ = ["CloneResult", "CommitInfo", "DefaultGitCache", "GitCache"]
