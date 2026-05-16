"""Shared helpers for assembling wiki prompt context (index head + log tail)."""

from __future__ import annotations

from pathlib import Path

_INDEX_HEAD_LINES = 60
_LOG_TAIL_ENTRIES = 15


def index_head(wiki_dir: Path, *, max_lines: int = _INDEX_HEAD_LINES) -> str:
    path = wiki_dir / "index.md"
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[:max_lines])


def log_tail(wiki_dir: Path, *, max_entries: int = _LOG_TAIL_ENTRIES) -> str:
    path = wiki_dir / "log.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    entry_starts: list[int] = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if not entry_starts:
        return ""
    start_idx = entry_starts[-max_entries] if len(entry_starts) > max_entries else entry_starts[0]
    return "\n".join(lines[start_idx:])
