"""Tests for the `cadence-memory init` CLI command (design2 §11)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app


def test_cli_init_in_explicit_target_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert "created: config.yaml" in result.stdout
    assert "created: CLAUDE.md" in result.stdout
    assert "created: .gitignore" in result.stdout
    assert "created: index.md" in result.stdout
    assert "git initialized" in result.stdout
    assert (tmp_path / "config.yaml").is_file()
    assert (tmp_path / ".git").is_dir()


def test_cli_init_default_target_is_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "config.yaml").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()
    assert (tmp_path / "index.md").is_file()


def test_cli_init_idempotent_second_run_prints_exists(tmp_path: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(app, ["init", str(tmp_path)])
    assert first.exit_code == 0, first.stdout

    second = runner.invoke(app, ["init", str(tmp_path)])

    assert second.exit_code == 0, second.stdout
    assert "exists: config.yaml" in second.stdout
    assert "created:" not in second.stdout
