"""Tests for the `cadence-memory init` command."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory import cli
from cadence_memory.cli import app
from cadence_memory.config import load_annotations_config, load_config
from cadence_memory.store.sqlite_store import SqliteStore

runner = CliRunner()


def test_init_creates_expected_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run_git_init", lambda _directory: None)

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "config.yaml").is_file()
    assert (tmp_path / "annotations-config.yaml").is_file()
    assert (tmp_path / ".gitignore").is_file()
    assert (tmp_path / "ephemeral").is_dir()
    assert (tmp_path / "ephemeral" / ".gitkeep").is_file()
    assert (tmp_path / "index.sqlite").is_file()
    assert "next steps" in result.output
    assert "created: config.yaml" in result.output


def test_init_refuses_to_overwrite_existing_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_run_git_init", lambda _directory: None)
    (tmp_path / "config.yaml").write_text("projects: []\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 1
    assert "config.yaml" in result.output
    assert "already exists" in result.output


def test_init_refuses_when_gitignore_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_run_git_init", lambda _directory: None)
    sentinel = "# user's existing rules\nnode_modules/\n"
    (tmp_path / ".gitignore").write_text(sentinel, encoding="utf-8")

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 1
    assert ".gitignore" in result.output
    assert "already exists" in result.output
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == sentinel
    assert not (tmp_path / "config.yaml").exists()
    assert not (tmp_path / "annotations-config.yaml").exists()


def test_init_refuses_when_annotations_config_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_run_git_init", lambda _directory: None)
    sentinel = "documents:\n  - id: leftover:foo.md\n"
    (tmp_path / "annotations-config.yaml").write_text(sentinel, encoding="utf-8")

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 1
    assert "annotations-config.yaml" in result.output
    assert (tmp_path / "annotations-config.yaml").read_text(encoding="utf-8") == sentinel
    assert not (tmp_path / "config.yaml").exists()
    assert not (tmp_path / ".gitignore").exists()


def test_init_invokes_git_init_in_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, "cwd": kwargs.get("cwd"), "check": kwargs.get("check")})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls == [
        {"cmd": ["git", "init", "-q"], "cwd": str(tmp_path), "check": True},
    ]


def test_init_warns_when_git_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "warning: git init failed" in result.output
    assert (tmp_path / "config.yaml").is_file()


def test_init_warns_when_git_init_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=128, cmd=cmd)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "warning: git init failed" in result.output
    assert (tmp_path / "config.yaml").is_file()


def test_init_skips_git_init_if_dot_git_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls == []


def test_init_templates_parse_through_loaders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_run_git_init", lambda _directory: None)

    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output

    cfg = load_config(tmp_path / "config.yaml")
    annotations = load_annotations_config(tmp_path / "annotations-config.yaml", config=cfg)
    assert cfg.projects == ()
    assert cfg.defaults.kind == "doc"
    assert cfg.commit_index is False
    assert annotations.documents == ()


def test_init_creates_openable_sqlite_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_run_git_init", lambda _directory: None)

    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output

    store = SqliteStore(tmp_path / "index.sqlite")
    try:
        assert store.all_ids() == set()
    finally:
        store.close()


def test_version_flag_still_works() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "cadence-memory" in result.output
