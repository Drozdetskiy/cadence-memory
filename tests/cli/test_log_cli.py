"""Tests for the `cadence-memory log` CLI sub-app."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.worker.log_rotate import LogRotationDuplicateError, LogRotationError

_LOG_FM = (
    "---\n"
    'title: "Activity Log"\n'
    "type: log\n"
    "project: _master\n"
    "created: 2026-05-12\n"
    "updated: 2026-05-12\n"
    "tags: []\n"
    "confidence: high\n"
    "---\n"
    "\n"
)

_ENTRY_HEADER_PREFIX = "## ["


def _git(wiki: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=wiki,
        check=True,
        text=True,
        capture_output=True,
    )


def _init_wiki(tmp_path: Path, log_content: str) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git(wiki, "init", "--initial-branch=main")
    _git(wiki, "config", "user.email", "test@example.com")
    _git(wiki, "config", "user.name", "Test User")
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    (wiki / "log.md").write_text(log_content, encoding="utf-8")
    (wiki / "config.yaml").write_text("repos: []\n", encoding="utf-8")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-m", "seed")
    return wiki


def _make_log(months_entries: dict[str, int]) -> str:
    parts: list[str] = []
    for ym, count in months_entries.items():
        for i in range(count):
            day = (i % 28) + 1
            date_iso = f"{ym}-{day:02d}"
            parts.append(f"## [{date_iso}] entry {i}\n\nBody {i}.\n\n")
    return _LOG_FM + "".join(parts)


def _fixed_clock(year: int, month: int, day: int) -> Callable[[], datetime]:
    def _inner() -> datetime:
        return datetime(year, month, day, tzinfo=UTC)

    return _inner


def test_log_rotate_dry_run_plan_output(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path, _make_log({"2026-03": 50, "2026-04": 50, "2026-05": 100}))
    runner = CliRunner()

    with patch(
        "cadence_memory.cli_commands.log.rotate_log",
        wraps=lambda **kw: __import__(
            "cadence_memory.worker.log_rotate", fromlist=["rotate_log"]
        ).rotate_log(**{**kw, "clock": _fixed_clock(2026, 5, 14)}),
    ):
        result = runner.invoke(app, ["log", "rotate", "--dry-run", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    assert "planned" in result.output
    assert "2026-03" in result.output


def test_log_rotate_applies_changes(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path, _make_log({"2026-03": 50, "2026-04": 50, "2026-05": 100}))
    runner = CliRunner()

    with patch(
        "cadence_memory.cli_commands.log.rotate_log",
        wraps=lambda **kw: __import__(
            "cadence_memory.worker.log_rotate", fromlist=["rotate_log"]
        ).rotate_log(**{**kw, "clock": _fixed_clock(2026, 5, 14)}),
    ):
        result = runner.invoke(app, ["log", "rotate", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    assert (wiki / "log" / "2026-03.md").exists()
    log_result = _git(wiki, "log", "--oneline", "-2")
    assert "chore: rotate log.md (2026-03 archive)" in log_result.stdout
    assert "rotated:" in result.output


def test_log_rotate_handles_missing_wiki(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    runner = CliRunner()

    result = runner.invoke(app, ["log", "rotate", "--wiki", str(empty)])

    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_log_rotate_handles_duplicate_archive(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path, _make_log({"2026-03": 50, "2026-04": 50, "2026-05": 100}))
    runner = CliRunner()

    def _raise_duplicate(**kw: object) -> object:
        raise LogRotationDuplicateError(
            "duplicate archive entry: ## [2026-03-01] already in log/2026-03.md"
        )

    with patch("cadence_memory.cli_commands.log.rotate_log", side_effect=_raise_duplicate):
        result = runner.invoke(app, ["log", "rotate", "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "duplicate" in result.output or "duplicate" in (result.stderr or "")


def test_log_rotate_no_op_output(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path, _make_log({"2026-05": 5}))
    runner = CliRunner()

    with patch(
        "cadence_memory.cli_commands.log.rotate_log",
        wraps=lambda **kw: __import__(
            "cadence_memory.worker.log_rotate", fromlist=["rotate_log"]
        ).rotate_log(**{**kw, "clock": _fixed_clock(2026, 5, 14)}),
    ):
        result = runner.invoke(app, ["log", "rotate", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    assert "no-op" in result.output


def test_log_rotate_error_exits_1(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path, _make_log({"2026-03": 50, "2026-04": 50, "2026-05": 100}))
    runner = CliRunner()

    def _raise_error(**kw: object) -> object:
        raise LogRotationError("git commit failed")

    with patch("cadence_memory.cli_commands.log.rotate_log", side_effect=_raise_error):
        result = runner.invoke(app, ["log", "rotate", "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "git commit failed" in result.output or "git commit failed" in (result.stderr or "")
