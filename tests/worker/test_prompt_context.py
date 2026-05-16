"""Tests for shared wiki prompt-context helpers."""

from __future__ import annotations

from pathlib import Path

from cadence_memory.worker.prompt_context import index_head, log_tail


def test_index_head_returns_first_n_lines(tmp_path: Path) -> None:
    lines = [f"line {i}" for i in range(200)]
    (tmp_path / "index.md").write_text("\n".join(lines), encoding="utf-8")

    result = index_head(tmp_path)

    assert result == "\n".join(lines[:60])


def test_index_head_short_file_returned_whole(tmp_path: Path) -> None:
    lines = [f"line {i}" for i in range(10)]
    (tmp_path / "index.md").write_text("\n".join(lines), encoding="utf-8")

    result = index_head(tmp_path)

    assert result == "\n".join(lines)


def test_index_head_missing_file_returns_empty(tmp_path: Path) -> None:
    assert index_head(tmp_path) == ""


def test_index_head_respects_max_lines_override(tmp_path: Path) -> None:
    lines = [f"line {i}" for i in range(200)]
    (tmp_path / "index.md").write_text("\n".join(lines), encoding="utf-8")

    result = index_head(tmp_path, max_lines=5)

    assert result == "\n".join(lines[:5])
    assert result.count("\n") == 4


def test_log_tail_returns_last_n_entries(tmp_path: Path) -> None:
    entries = [f"## [2026-01-{i:02d}] entry {i}\nbody {i}" for i in range(1, 31)]
    (tmp_path / "log.md").write_text("\n".join(entries), encoding="utf-8")

    result = log_tail(tmp_path)

    headers = [line for line in result.splitlines() if line.startswith("## ")]
    assert len(headers) == 15
    assert headers[0] == "## [2026-01-16] entry 16"
    assert headers[-1] == "## [2026-01-30] entry 30"


def test_log_tail_returns_all_when_fewer_than_max(tmp_path: Path) -> None:
    entries = [f"## [2026-01-{i:02d}] entry {i}\nbody {i}" for i in range(1, 6)]
    text = "\n".join(entries)
    (tmp_path / "log.md").write_text(text, encoding="utf-8")

    result = log_tail(tmp_path)

    assert result == text


def test_log_tail_missing_file_returns_empty(tmp_path: Path) -> None:
    assert log_tail(tmp_path) == ""


def test_log_tail_no_headers_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "log.md").write_text("just some text\nno headers here\n", encoding="utf-8")

    assert log_tail(tmp_path) == ""


def test_log_tail_respects_max_entries_override(tmp_path: Path) -> None:
    entries = [f"## [2026-01-{i:02d}] entry {i}\nbody {i}" for i in range(1, 31)]
    (tmp_path / "log.md").write_text("\n".join(entries), encoding="utf-8")

    result = log_tail(tmp_path, max_entries=3)

    headers = [line for line in result.splitlines() if line.startswith("## ")]
    assert len(headers) == 3
    assert headers[0] == "## [2026-01-28] entry 28"
    assert headers[-1] == "## [2026-01-30] entry 30"
