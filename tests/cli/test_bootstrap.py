"""Tests for the `cadence-memory bootstrap` alias and `worker run --mode bootstrap` (design2 §8)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.config.schema import RepoConfig
from cadence_memory.config.writer import add_repo
from cadence_memory.wiki import scaffold_wiki
from cadence_memory.worker.bootstrap import BootstrapOutcome
from cadence_memory.worker.state import load_state
from tests._helpers import strip_ansi

_HEAD_SHA = "a" * 40


def _scaffold(tmp_path: Path) -> Path:
    scaffold_wiki(tmp_path)
    add_repo(
        tmp_path / "config.yaml",
        name="proj",
        url="git@github.com:org/proj.git",
        branch="main",
    )
    return tmp_path


@dataclass
class _BootstrapCall:
    repo_cfg: RepoConfig
    wiki_dir: Path
    kwargs: dict[str, Any]


def _fake_run_bootstrap_factory(
    *,
    stages_failed: tuple[int, ...] = (),
    head_sha: str = _HEAD_SHA,
    cost_usd_total: float = 0.42,
    pages_touched_total: int = 7,
    record: list[_BootstrapCall] | None = None,
) -> Any:
    def fake_run_bootstrap(**kwargs: Any) -> BootstrapOutcome:
        if record is not None:
            record.append(
                _BootstrapCall(
                    repo_cfg=kwargs["repo_cfg"],
                    wiki_dir=kwargs["wiki_dir"],
                    kwargs=kwargs,
                )
            )
        return BootstrapOutcome(
            stages_run=(1, 2, 3, 4, 5),
            stages_failed=stages_failed,
            head_sha=head_sha,
            cost_usd_total=cost_usd_total,
            pages_touched_total=pages_touched_total,
        )

    return fake_run_bootstrap


def test_cli_bootstrap_alias_calls_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scaffold(tmp_path)
    captured: list[_BootstrapCall] = []
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_bootstrap",
        _fake_run_bootstrap_factory(record=captured),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["bootstrap", "proj", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    assert captured[0].repo_cfg.name == "proj"
    assert captured[0].wiki_dir == tmp_path.resolve()


def test_cli_mode_bootstrap_requires_only(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["worker", "run", "--mode", "bootstrap", "--wiki", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "requires --only" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_mode_bootstrap_unknown_repo(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "worker",
            "run",
            "--mode",
            "bootstrap",
            "--only",
            "ghost",
            "--wiki",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "unknown repo" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_state_advances_on_partial_failure_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_bootstrap",
        _fake_run_bootstrap_factory(stages_failed=(2,), head_sha=_HEAD_SHA),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["bootstrap", "proj", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    state = load_state(tmp_path / ".cadence-memory" / "state.json")
    assert state.repos["proj"].last_sha == _HEAD_SHA
    assert state.repos["proj"].last_failure is not None
    assert "bootstrap-2" in state.repos["proj"].last_failure


def test_cli_strict_blocks_state_advance_on_any_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_bootstrap",
        _fake_run_bootstrap_factory(stages_failed=(2,), head_sha=_HEAD_SHA),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["bootstrap", "proj", "--wiki", str(tmp_path), "--strict"])

    assert result.exit_code == 1
    state = load_state(tmp_path / ".cadence-memory" / "state.json")
    assert state.repos["proj"].last_sha is None
    assert state.repos["proj"].last_failure is not None
    assert "bootstrap-2" in state.repos["proj"].last_failure


def test_cli_state_advances_on_clean_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_bootstrap",
        _fake_run_bootstrap_factory(stages_failed=(), head_sha=_HEAD_SHA),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["bootstrap", "proj", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    state = load_state(tmp_path / ".cadence-memory" / "state.json")
    assert state.repos["proj"].last_sha == _HEAD_SHA
    assert state.repos["proj"].last_failure is None


def test_cli_exit_code_1_when_any_stage_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_bootstrap",
        _fake_run_bootstrap_factory(stages_failed=(3,)),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "worker",
            "run",
            "--mode",
            "bootstrap",
            "--only",
            "proj",
            "--wiki",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1


def test_cli_exit_code_0_on_clean_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_bootstrap",
        _fake_run_bootstrap_factory(stages_failed=(), cost_usd_total=0.33),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "worker",
            "run",
            "--mode",
            "bootstrap",
            "--only",
            "proj",
            "--wiki",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "stages run 5" in result.stdout
    assert "failed 0" in result.stdout
    assert "$0.33" in result.stdout


def test_cli_bootstrap_help_lists_strict() -> None:
    runner = CliRunner()

    worker_help = runner.invoke(app, ["worker", "run", "--help"])
    assert worker_help.exit_code == 0, worker_help.stdout
    assert "--strict" in strip_ansi(worker_help.stdout)

    boot_help = runner.invoke(app, ["bootstrap", "--help"])
    assert boot_help.exit_code == 0, boot_help.stdout
    assert "--strict" in strip_ansi(boot_help.stdout)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock-based lock test")
def test_cli_bootstrap_holds_worker_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli_commands.worker.run_bootstrap",
        _fake_run_bootstrap_factory(),
    )
    lock_path = tmp_path / ".cadence-memory" / "worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            runner = CliRunner()
            result = runner.invoke(app, ["bootstrap", "proj", "--wiki", str(tmp_path)])
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

    assert result.exit_code == 1
    assert "worker.lock" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
