"""Tests for the wiki commit helpers (design2 §7)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cadence_memory.worker.wiki_commit import (
    WikiCommitError,
    append_log_failure,
    list_touched_paths,
    revert_wiki,
    stage_and_commit,
)


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
    (wiki / "log.md").write_text("# log\n", encoding="utf-8")
    _git("add", "-A", cwd=wiki)
    _git("commit", "-m", "seed", cwd=wiki)
    return wiki


def test_stage_and_commit_returns_sha_on_change(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "page.md").write_text("hello\n", encoding="utf-8")

    sha = stage_and_commit(wiki_dir=wiki, message="add page")

    assert sha is not None
    assert len(sha) == 40
    head = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head == sha
    subject = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert subject == "add page"


def test_stage_and_commit_returns_none_when_clean(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()

    result = stage_and_commit(wiki_dir=wiki, message="noop")

    assert result is None
    head_after = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head_before == head_after


def test_stage_and_commit_raises_on_git_failure(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    with pytest.raises(WikiCommitError):
        stage_and_commit(wiki_dir=not_a_repo, message="boom")


def test_list_touched_paths_includes_modified_and_untracked(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "index.md").write_text("# index modified\n", encoding="utf-8")
    (wiki / "new.md").write_text("# new\n", encoding="utf-8")

    touched = list_touched_paths(wiki)

    rel = {p.relative_to(wiki.resolve()) for p in touched}
    assert Path("index.md") in rel
    assert Path("new.md") in rel


def test_list_touched_paths_omits_deleted(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "index.md").unlink()

    touched = list_touched_paths(wiki)

    rel = {p.relative_to(wiki.resolve()) for p in touched}
    assert Path("index.md") not in rel


def test_list_touched_paths_includes_added_then_modified(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    new_file = wiki / "added.md"
    new_file.write_text("first\n", encoding="utf-8")
    _git("add", "added.md", cwd=wiki)
    new_file.write_text("second\n", encoding="utf-8")

    touched = list_touched_paths(wiki)

    rel = {p.relative_to(wiki.resolve()) for p in touched}
    assert Path("added.md") in rel


def test_list_touched_paths_recurses_into_new_directories(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "projects" / "project-a").mkdir(parents=True)
    (wiki / "projects" / "project-a" / "overview.md").write_text("hi\n", encoding="utf-8")
    (wiki / "projects" / "project-a" / "notes.md").write_text("hi2\n", encoding="utf-8")

    touched = list_touched_paths(wiki)

    rel = {p.relative_to(wiki.resolve()) for p in touched}
    assert Path("projects/project-a/overview.md") in rel
    assert Path("projects/project-a/notes.md") in rel


def test_revert_wiki_restores_modified_and_removes_untracked(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "index.md").write_text("# index modified\n", encoding="utf-8")
    (wiki / "stub.md").write_text("# stub\n", encoding="utf-8")
    (wiki / "subdir").mkdir()
    (wiki / "subdir" / "nested.md").write_text("nested\n", encoding="utf-8")

    revert_wiki(wiki)

    assert (wiki / "index.md").read_text(encoding="utf-8") == "# index\n"
    assert not (wiki / "stub.md").exists()
    assert not (wiki / "subdir").exists()


def test_append_log_failure_commits_entry(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()

    append_log_failure(
        wiki_dir=wiki,
        repo_name="proj-a",
        short_sha="abc1234",
        subject="something broke",
        error="boom traceback",
        today_iso="2026-05-12",
    )

    head_after = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head_after != head_before
    subject = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert subject == "cadence-memory: ingest failure proj-a abc1234"
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "## [2026-05-12] FAILED ingest | proj-a abc1234 — something broke" in log_text
    assert "boom traceback" in log_text
