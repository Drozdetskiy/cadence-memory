"""Tests for the `cadence-memory worker daemon` CLI command (design2 §11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.config.loader import load_config
from cadence_memory.config.writer import add_repo
from cadence_memory.wiki import scaffold_wiki
from cadence_memory.worker.daemon import DaemonSignals
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


def _fake_run_pending(**_kwargs: Any) -> tuple[WorkerState, RunSummary]:
    return (
        WorkerState(),
        RunSummary(repos=("proj",), events_processed=0, events_failed=0, cost_usd_total=0.0),
    )


def test_cli_daemon_help_shows_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["worker", "daemon", "--help"])

    assert result.exit_code == 0, result.stdout
    plain = strip_ansi(result.stdout)
    assert "--once" in plain
    assert "--wiki" in plain


def test_cli_once_delegates_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)
    run_pending_calls: list[dict[str, Any]] = []

    def fake_run_pending(**kwargs: Any) -> tuple[WorkerState, RunSummary]:
        run_pending_calls.append(kwargs)
        return _fake_run_pending(**kwargs)

    run_daemon_calls: list[dict[str, Any]] = []

    def fake_run_daemon(**kwargs: Any) -> None:
        run_daemon_calls.append(kwargs)

    monkeypatch.setattr("cadence_memory.cli_commands.worker.run_pending", fake_run_pending)
    monkeypatch.setattr("cadence_memory.cli_commands.worker.run_daemon", fake_run_daemon)
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "daemon", "--once", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(run_pending_calls) == 1
    assert run_daemon_calls == []
    kwargs = run_pending_calls[0]
    assert kwargs["wiki_dir"] == tmp_path.resolve()
    assert kwargs["only_repo"] is None
    assert kwargs["limit"] is None
    assert kwargs["dry_run"] is False


def test_cli_daemon_invokes_run_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)
    captured: list[dict[str, Any]] = []

    def fake_run_daemon(**kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr("cadence_memory.cli_commands.worker.run_daemon", fake_run_daemon)
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "daemon", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    kwargs = captured[0]
    assert kwargs["wiki_dir"] == tmp_path.resolve()
    expected_poll = load_config(tmp_path / "config.yaml").worker.poll_interval_s
    assert kwargs["poll_interval_s"] == expected_poll
    assert callable(kwargs["config_factory"])
    assert callable(kwargs["cache_factory"])
    assert callable(kwargs["runner_factory"])
    assert kwargs["state_path"] == tmp_path.resolve() / ".cadence-memory" / "state.json"
    assert isinstance(kwargs["signals"], DaemonSignals)


def test_cli_daemon_no_wiki_found_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.delenv("CADENCE_MEMORY_WIKI", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "daemon"])

    assert result.exit_code == 1
    assert result.stderr.strip() != ""
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_daemon_malformed_config_exits_1(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "config.yaml").write_text(":\n  - bad: yaml:\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "daemon", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert result.stderr.strip() != ""
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_daemon_exits_130_on_sigint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)

    def fake_run_daemon(**kwargs: Any) -> None:
        signals = kwargs["signals"]
        signals.interrupt.set()

    monkeypatch.setattr("cadence_memory.cli_commands.worker.run_daemon", fake_run_daemon)
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "daemon", "--wiki", str(tmp_path)])

    assert result.exit_code == 130


def test_cli_daemon_exits_0_on_sigterm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)

    def fake_run_daemon(**kwargs: Any) -> None:
        signals = kwargs["signals"]
        signals.terminate.set()

    monkeypatch.setattr("cadence_memory.cli_commands.worker.run_daemon", fake_run_daemon)
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "daemon", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
