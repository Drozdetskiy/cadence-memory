"""Tests for the `cadence-memory repos` CLI sub-app (design2 §11)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.wiki import scaffold_wiki


def _scaffold(tmp_path: Path) -> Path:
    scaffold_wiki(tmp_path)
    return tmp_path / "config.yaml"


def test_cli_repos_help_lists_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["repos", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "add" in result.stdout
    assert "list" in result.stdout
    assert "remove" in result.stdout


def test_cli_add_invocation_round_trips(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    add = runner.invoke(
        app,
        ["repos", "add", "foo", "git@github.com:org/y.git", "--wiki", str(tmp_path)],
    )
    assert add.exit_code == 0, add.stdout + add.stderr
    assert "added: foo" in add.stdout

    listed = runner.invoke(app, ["repos", "list", "--wiki", str(tmp_path)])
    assert listed.exit_code == 0, listed.stdout + listed.stderr
    assert "foo" in listed.stdout
    assert "git@github.com:org/y.git" in listed.stdout
    assert "main" in listed.stdout


def test_cli_resolves_wiki_from_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scaffold(tmp_path)
    elsewhere = tmp_path.parent / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("CADENCE_MEMORY_WIKI", str(tmp_path))
    monkeypatch.chdir(elsewhere)

    runner = CliRunner()
    result = runner.invoke(app, ["repos", "list"])

    assert result.exit_code == 0, result.stdout + result.stderr


def test_cli_add_duplicate_exits_1_no_traceback(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    first = runner.invoke(
        app,
        ["repos", "add", "foo", "git@github.com:org/y.git", "--wiki", str(tmp_path)],
    )
    assert first.exit_code == 0, first.stdout + first.stderr

    dup = runner.invoke(
        app,
        ["repos", "add", "foo", "git@github.com:org/y.git", "--wiki", str(tmp_path)],
    )

    assert dup.exit_code == 1
    assert "already exists" in dup.stderr
    assert "Traceback" not in dup.stdout
    assert "Traceback" not in dup.stderr


def test_cli_add_bad_slug_exits_1(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["repos", "add", "Foo_Bar", "git@github.com:org/y.git", "--wiki", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "not a valid slug" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_remove_unknown_exits_1(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["repos", "remove", "ghost", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert "no repo named" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_remove_success_round_trips(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    add = runner.invoke(
        app,
        ["repos", "add", "foo", "git@github.com:org/y.git", "--wiki", str(tmp_path)],
    )
    assert add.exit_code == 0, add.stdout + add.stderr

    removed = runner.invoke(app, ["repos", "remove", "foo", "--wiki", str(tmp_path)])
    assert removed.exit_code == 0, removed.stdout + removed.stderr
    assert "removed: foo" in removed.stdout

    listed = runner.invoke(app, ["repos", "list", "--format", "json", "--wiki", str(tmp_path)])
    assert listed.exit_code == 0
    assert json.loads(listed.stdout) == []


def test_cli_add_optional_flags_thread_through(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    add = runner.invoke(
        app,
        [
            "repos",
            "add",
            "alpha",
            "git@github.com:org/alpha.git",
            "--branch",
            "dev",
            "--start-commit",
            "abc123",
            "--model",
            "claude-opus-4-7",
            "--budget",
            "1.25",
            "--wiki",
            str(tmp_path),
        ],
    )
    assert add.exit_code == 0, add.stdout + add.stderr

    listed = runner.invoke(app, ["repos", "list", "--format", "json", "--wiki", str(tmp_path)])
    assert listed.exit_code == 0
    entries = json.loads(listed.stdout)
    assert len(entries) == 1
    repo = entries[0]
    assert repo["name"] == "alpha"
    assert repo["branch"] == "dev"
    assert repo["start_commit"] == "abc123"
    assert repo["model"] == "claude-opus-4-7"
    assert repo["budget_usd"] == 1.25


def test_cli_no_wiki_found_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CADENCE_MEMORY_WIKI", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["repos", "list"])

    assert result.exit_code == 1
    assert result.stderr.strip() != ""
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_malformed_config_exits_cleanly(tmp_path: Path) -> None:
    cfg = _scaffold(tmp_path)
    cfg.write_text(":\n  - this is: not valid: yaml:\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["repos", "list", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert result.stderr.strip() != ""
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_list_json_format_parses(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()
    runner.invoke(
        app,
        ["repos", "add", "alpha", "git@github.com:org/a.git", "--wiki", str(tmp_path)],
    )
    runner.invoke(
        app,
        ["repos", "add", "beta", "git@github.com:org/b.git", "--wiki", str(tmp_path)],
    )

    result = runner.invoke(app, ["repos", "list", "--format", "json", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    names = [entry["name"] for entry in parsed]
    assert names == ["alpha", "beta"]


def test_cli_list_invalid_format_exits_1(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["repos", "list", "--format", "xml", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert "--format" in result.stderr
