"""Tests for the `cadence-memory lint` CLI command (design2 §9, §11)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.wiki import scaffold_wiki
from cadence_memory.worker.lint import LintOutcome

_OK_SHA = "abc1234"
_BRANCH = "lint/2026-05-12"


def _scaffold(tmp_path: Path) -> Path:
    scaffold_wiki(tmp_path)
    return tmp_path


def _scaffold_with_color(tmp_path: Path) -> Path:
    wiki = _scaffold(tmp_path)
    config_path = wiki / "config.yaml"
    config_path.write_text(config_path.read_text() + "progress:\n  color: always\n")
    return wiki


@dataclass
class _LintCall:
    kwargs: dict[str, Any]


def _fake_run_lint_factory(
    *,
    outcome: LintOutcome,
    record: list[_LintCall] | None = None,
) -> Any:
    def fake_run_lint(**kwargs: Any) -> LintOutcome:
        if record is not None:
            record.append(_LintCall(kwargs=kwargs))
        return outcome

    return fake_run_lint


def _success_outcome(
    *,
    cost_usd: float | None = 0.42,
    pages: tuple[Path, ...] = (),
    sha: str | None = _OK_SHA,
    branch_used: str = _BRANCH,
    previous_branch: str | None = "main",
) -> LintOutcome:
    return LintOutcome(
        success=True,
        pages_touched=pages,
        branch_used=branch_used,
        previous_branch=previous_branch,
        wiki_commit_sha=sha,
        cost_usd=cost_usd,
        error=None,
    )


def _failure_outcome(
    *,
    error: str | None = "boom",
    branch_used: str = _BRANCH,
    previous_branch: str | None = "main",
) -> LintOutcome:
    return LintOutcome(
        success=False,
        pages_touched=(),
        branch_used=branch_used,
        previous_branch=previous_branch,
        wiki_commit_sha=None,
        cost_usd=0.01,
        error=error,
    )


def test_cli_lint_exit_0_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    outcome = _success_outcome(
        cost_usd=0.42,
        pages=(wiki / "learnings.md", wiki / "gaps.md"),
    )
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=outcome),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "ok:" in result.stdout
    assert _OK_SHA in result.stdout
    assert "2 pages" in result.stdout
    assert "$0.42" in result.stdout
    assert _BRANCH in result.stdout
    assert "switch back with `git checkout main`" in result.stdout


def test_cli_lint_exit_1_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=_failure_outcome(error="claude went sideways")),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "failed:" in result.stderr
    assert "claude went sideways" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    assert "switch back with `git checkout main`" in result.stdout


def test_cli_lint_exit_1_on_failure_with_none_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=_failure_outcome(error=None)),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "failed:" in result.stderr
    assert "None" not in result.stderr
    assert "claude run failed" in result.stderr


def test_cli_lint_apply_passes_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    captured: list[_LintCall] = []
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(
            outcome=_success_outcome(previous_branch=None, branch_used="feature/x"),
            record=captured,
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--apply", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    assert captured[0].kwargs["apply"] is True
    assert "switch back" not in result.stdout


def test_cli_lint_only_passes_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    captured: list[_LintCall] = []
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=_success_outcome(), record=captured),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["lint", "--only", "project-a", "--wiki", str(wiki)],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    assert captured[0].kwargs["only_repo"] == "project-a"


def test_cli_lint_default_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    captured: list[_LintCall] = []
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=_success_outcome(), record=captured),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    assert captured[0].kwargs["apply"] is False
    assert captured[0].kwargs["only_repo"] is None


def test_cli_lint_exit_1_on_wiki_not_found(tmp_path: Path) -> None:
    nowhere = tmp_path / "no-wiki-here"
    nowhere.mkdir()
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--wiki", str(nowhere)])

    assert result.exit_code == 1
    assert "config.yaml" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_lint_exit_1_on_bad_config(tmp_path: Path) -> None:
    wiki = _scaffold(tmp_path)
    (wiki / "config.yaml").write_text(": invalid: yaml: [[[\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "invalid YAML" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_lint_cost_and_sha_none_render_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    outcome = LintOutcome(
        success=True,
        pages_touched=(),
        branch_used=_BRANCH,
        previous_branch="main",
        wiki_commit_sha=None,
        cost_usd=None,
        error=None,
    )
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=outcome),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "ok:" in result.stdout
    assert "no changes" in result.stdout
    assert "(n/a)" in result.stdout
    assert "None" not in result.stdout
    assert _BRANCH in result.stdout


def test_cli_lint_phase_header_and_summary_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=_success_outcome(cost_usd=0.05, sha=_OK_SHA)),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["lint", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    # (a) phase header
    assert "running lint" in result.stdout
    # (b) final summary present
    assert "ok:" in result.stdout
    assert _OK_SHA in result.stdout


def test_cli_lint_no_color_strips_ansi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold_with_color(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=_success_outcome()),
    )
    runner = CliRunner()

    result_color = runner.invoke(app, ["lint", "--wiki", str(wiki)])
    assert "\x1b[" in result_color.stdout

    result_plain = runner.invoke(app, ["--no-color", "lint", "--wiki", str(wiki)])
    assert "\x1b[" not in result_plain.stdout
    assert "ok:" in result_plain.stdout


def test_cli_lint_quiet_suppresses_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    monkeypatch.setattr(
        "cadence_memory.cli.run_lint",
        _fake_run_lint_factory(outcome=_success_outcome()),
    )
    runner = CliRunner()

    result_quiet = runner.invoke(app, ["--quiet", "lint", "--wiki", str(wiki)])
    assert result_quiet.exit_code == 0, result_quiet.stdout + result_quiet.stderr
    assert "running lint" not in result_quiet.stdout
    assert "ok:" in result_quiet.stdout
