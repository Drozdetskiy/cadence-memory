"""Tests for the `cadence-memory worker` CLI sub-app (design2 §6.1, §11)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.config.writer import add_repo
from cadence_memory.wiki import scaffold_wiki
from cadence_memory.worker.run import RunSummary
from cadence_memory.worker.state import WorkerState
from tests._helpers import strip_ansi


def _scaffold(tmp_path: Path) -> Path:
    scaffold_wiki(tmp_path)
    add_repo(
        tmp_path / "config.yaml",
        name="proj",
        url="git@github.com:org/proj.git",
        branch="main",
    )
    return tmp_path


def _scaffold_with_color(tmp_path: Path) -> Path:
    _scaffold(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_path.read_text() + "progress:\n  color: always\n")
    return tmp_path


def _fake_run_pending_factory(
    *,
    events_processed: int = 0,
    events_failed: int = 0,
    cost_usd_total: float = 0.0,
    record: list[dict[str, Any]] | None = None,
) -> Any:
    def fake_run_pending(**kwargs: Any) -> tuple[WorkerState, RunSummary]:
        if record is not None:
            record.append(kwargs)
        summary = RunSummary(
            repos=("proj",),
            events_processed=events_processed,
            events_failed=events_failed,
            cost_usd_total=cost_usd_total,
        )
        return WorkerState(), summary

    return fake_run_pending


def test_cli_worker_help_lists_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["worker", "--help"])

    assert result.exit_code == 0, result.stdout
    assert "run" in result.stdout
    assert "daemon" in result.stdout


def test_cli_worker_run_help_shows_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["worker", "run", "--help"])

    assert result.exit_code == 0, result.stdout
    plain = strip_ansi(result.stdout)
    assert "--mode" in plain
    assert "--only" in plain
    assert "--limit" in plain
    assert "--dry-run" in plain
    assert "--wiki" in plain


def test_cli_run_dry_run_passes_flag_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scaffold(tmp_path)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_pending",
        _fake_run_pending_factory(record=captured),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run", "--dry-run", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    assert captured[0]["dry_run"] is True


def test_cli_run_exits_0_on_clean_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_pending",
        _fake_run_pending_factory(events_processed=2, events_failed=0, cost_usd_total=0.25),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "processed 2" in result.stdout
    assert "failed 0" in result.stdout
    assert "$0.25" in result.stdout


def test_cli_run_exits_1_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_pending",
        _fake_run_pending_factory(events_processed=1, events_failed=1, cost_usd_total=0.10),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert "processed 1" in result.stdout
    assert "failed 1" in result.stdout


def test_cli_run_bootstrap_mode_requires_only(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run", "--mode", "bootstrap", "--wiki", str(tmp_path)])

    assert result.exit_code == 2
    assert "requires --only" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_run_unknown_mode_errors(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run", "--mode", "foo", "--wiki", str(tmp_path)])

    assert result.exit_code == 2
    assert "--mode" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_run_no_wiki_found_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.delenv("CADENCE_MEMORY_WIKI", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run"])

    assert result.exit_code == 1
    assert result.stderr.strip() != ""
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_run_malformed_config_exits_1(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "config.yaml").write_text(":\n  - bad: yaml:\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert result.stderr.strip() != ""
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_run_malformed_state_exits_1(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    state_path = tmp_path / ".cadence-memory" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not-json", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert result.stderr.strip() != ""
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock-based lock test")
def test_cli_run_lock_held_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_pending",
        _fake_run_pending_factory(),
    )
    lock_path = tmp_path / ".cadence-memory" / "worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            runner = CliRunner()
            result = runner.invoke(app, ["worker", "run", "--wiki", str(tmp_path)])
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

    assert result.exit_code == 1
    assert "worker.lock" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_run_passes_only_and_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_pending",
        _fake_run_pending_factory(record=captured),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "worker",
            "run",
            "--only",
            "proj",
            "--limit",
            "5",
            "--wiki",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    assert captured[0]["only_repo"] == "proj"
    assert captured[0]["limit"] == 5


def test_cli_run_phase_header_and_summary_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_pending",
        _fake_run_pending_factory(events_processed=3, events_failed=0, cost_usd_total=0.15),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "run", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    # (a) phase header
    assert "starting worker run" in result.stdout
    # (b) final summary present
    assert "processed 3" in result.stdout
    assert "$0.15" in result.stdout


def test_cli_run_no_color_strips_ansi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold_with_color(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_pending",
        _fake_run_pending_factory(),
    )
    runner = CliRunner()

    result_color = runner.invoke(app, ["worker", "run", "--wiki", str(tmp_path)])
    assert "\x1b[" in result_color.stdout

    result_plain = runner.invoke(app, ["--no-color", "worker", "run", "--wiki", str(tmp_path)])
    assert "\x1b[" not in result_plain.stdout
    assert "processed 0" in result_plain.stdout


def test_cli_run_quiet_suppresses_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_pending",
        _fake_run_pending_factory(events_processed=1),
    )
    runner = CliRunner()

    result_quiet = runner.invoke(app, ["--quiet", "worker", "run", "--wiki", str(tmp_path)])
    assert result_quiet.exit_code == 0, result_quiet.stdout + result_quiet.stderr
    assert "starting worker run" not in result_quiet.stdout
    assert "processed 1" in result_quiet.stdout
