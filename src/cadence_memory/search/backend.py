"""Search backends: qmd (preferred), ripgrep (fallback)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Literal, Protocol

from cadence_memory.search.result import Hit


class SearchError(Exception):
    """Raised when a backend command runs but fails."""


class NoBackendAvailableError(Exception):
    """Raised when no search backend binary is on $PATH."""


class SearchBackend(Protocol):
    name: Literal["qmd", "ripgrep"]

    @staticmethod
    def available() -> bool: ...

    def search(self, *, query: str, wiki_dir: Path, limit: int) -> tuple[Hit, ...]: ...


class QmdBackend:
    name: Literal["qmd", "ripgrep"] = "qmd"

    @staticmethod
    def available() -> bool:
        return shutil.which("qmd") is not None

    def search(self, *, query: str, wiki_dir: Path, limit: int) -> tuple[Hit, ...]:
        try:
            proc = subprocess.run(
                [
                    "qmd",
                    "search",
                    "--collection",
                    "master",
                    "--json",
                    "--limit",
                    str(limit),
                    "--",
                    query,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise SearchError(f"qmd invocation failed: {exc}") from exc
        if proc.returncode != 0:
            raise SearchError(proc.stderr.strip() or proc.stdout.strip() or "qmd failed")
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise SearchError(f"qmd returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise SearchError("qmd JSON must be a list of entries")
        hits: list[Hit] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                raise SearchError("qmd JSON entry must be an object")
            raw_path = entry.get("path")
            if not isinstance(raw_path, str):
                raise SearchError("qmd JSON entry missing string 'path'")
            abs_path = Path(raw_path)
            if not abs_path.is_absolute():
                abs_path = (wiki_dir / abs_path).resolve()
            raw_score = entry.get("score")
            if raw_score is None:
                score: float | None = None
            elif isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
                score = float(raw_score)
            else:
                raise SearchError("qmd JSON entry 'score' must be a number or null")
            raw_snippet = entry.get("snippet")
            if raw_snippet is None:
                snippet = ""
            elif isinstance(raw_snippet, str):
                snippet = raw_snippet
            else:
                raise SearchError("qmd JSON entry 'snippet' must be a string or null")
            hits.append(
                Hit(
                    path=abs_path,
                    score=score,
                    snippet=snippet,
                    backend="qmd",
                )
            )
        return tuple(hits)


class RipgrepBackend:
    name: Literal["qmd", "ripgrep"] = "ripgrep"

    @staticmethod
    def available() -> bool:
        return shutil.which("rg") is not None

    def search(self, *, query: str, wiki_dir: Path, limit: int) -> tuple[Hit, ...]:
        try:
            proc = subprocess.run(
                [
                    "rg",
                    "--type",
                    "md",
                    "--line-number",
                    "--max-count",
                    "1",
                    "--max-columns",
                    "200",
                    "--no-heading",
                    "--",
                    query,
                    str(wiki_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise SearchError(f"ripgrep invocation failed: {exc}") from exc
        if proc.returncode == 1:
            return ()
        if proc.returncode != 0:
            raise SearchError(proc.stderr.strip() or proc.stdout.strip() or "ripgrep failed")
        hits: list[Hit] = []
        for line in proc.stdout.splitlines():
            if not line:
                continue
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            raw_path, _lineno, content = parts
            abs_path = Path(raw_path)
            if not abs_path.is_absolute():
                abs_path = (wiki_dir / abs_path).resolve()
            hits.append(
                Hit(
                    path=abs_path,
                    score=None,
                    snippet=content.strip(),
                    backend="ripgrep",
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)


def pick_backend() -> SearchBackend:
    """Return the preferred available backend; qmd > ripgrep."""
    if QmdBackend.available():
        return QmdBackend()
    if RipgrepBackend.available():
        return RipgrepBackend()
    raise NoBackendAvailableError(
        "neither qmd nor ripgrep on $PATH — install one to use 'cadence-memory query' "
        "(brew install qmd, or install ripgrep)"
    )


__all__ = [
    "NoBackendAvailableError",
    "QmdBackend",
    "RipgrepBackend",
    "SearchBackend",
    "SearchError",
    "pick_backend",
]
