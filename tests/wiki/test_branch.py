"""Tests for the wiki branch helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cadence_memory.wiki.branch import BranchError, create_or_switch_branch


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def _init_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git("init", "--initial-branch=main", cwd=wiki)
    _git("config", "user.email", "test@example.com", cwd=wiki)
    _git("config", "user.name", "Test User", cwd=wiki)
    (wiki / "index.md").write_text("# index\n", encoding="utf-8")
    _git("add", "-A", cwd=wiki)
    _git("commit", "-m", "seed", cwd=wiki)
    return wiki


def _head_branch(wiki: Path) -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wiki).stdout.strip()


def test_creates_new_branch_returns_previous(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    previous = create_or_switch_branch(wiki, "lint/2026-05-12")

    assert previous == "main"
    assert _head_branch(wiki) == "lint/2026-05-12"


def test_switches_to_existing_branch(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _git("branch", "lint/2026-05-12", cwd=wiki)

    previous = create_or_switch_branch(wiki, "lint/2026-05-12")

    assert previous == "main"
    assert _head_branch(wiki) == "lint/2026-05-12"


def test_no_op_when_already_on_target(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _git("checkout", "-b", "lint/2026-05-12", cwd=wiki)

    previous = create_or_switch_branch(wiki, "lint/2026-05-12")

    assert previous == "lint/2026-05-12"
    assert _head_branch(wiki) == "lint/2026-05-12"


def test_raises_on_git_failure(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "nope"
    not_a_repo.mkdir()

    with pytest.raises(BranchError):
        create_or_switch_branch(not_a_repo, "lint/2026-05-12")
