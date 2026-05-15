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
    stage_and_commit_paths,
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

    revert_wiki(wiki, preserve=frozenset())

    assert (wiki / "index.md").read_text(encoding="utf-8") == "# index\n"
    assert not (wiki / "stub.md").exists()
    assert not (wiki / "subdir").exists()


def test_revert_preserves_user_dirty_tracked_file(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "notes.md").write_text("# notes\n", encoding="utf-8")
    _git("add", "notes.md", cwd=wiki)
    _git("commit", "-m", "add notes", cwd=wiki)

    (wiki / "index.md").write_text("user edit\n", encoding="utf-8")
    (wiki / "notes.md").write_text("claude edit\n", encoding="utf-8")

    revert_wiki(wiki, preserve=frozenset({(wiki / "index.md").resolve()}))

    assert (wiki / "index.md").read_text(encoding="utf-8") == "user edit\n"
    assert (wiki / "notes.md").read_text(encoding="utf-8") == "# notes\n"


def test_revert_removes_claude_untracked_file(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "projects" / "foo").mkdir(parents=True)
    (wiki / "projects" / "foo" / "overview.md").write_text("stub\n", encoding="utf-8")

    revert_wiki(wiki)

    assert not (wiki / "projects" / "foo" / "overview.md").exists()
    assert not (wiki / "projects").exists()


def test_revert_preserves_user_untracked_file(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "raw" / "notes").mkdir(parents=True)
    (wiki / "raw" / "notes" / "draft.md").write_text("draft\n", encoding="utf-8")

    revert_wiki(wiki, preserve=frozenset({(wiki / "raw" / "notes" / "draft.md").resolve()}))

    assert (wiki / "raw" / "notes" / "draft.md").read_text(encoding="utf-8") == "draft\n"


def test_revert_no_op_when_nothing_changed(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()

    revert_wiki(wiki)

    head_after = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head_before == head_after
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=wiki,
        check=True,
        text=True,
        capture_output=True,
    )
    assert result.stdout.strip() == ""


def test_revert_preserves_subset_when_claude_also_touches_same_file(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "index.md").write_text("user edit\n", encoding="utf-8")
    (wiki / "index.md").write_text("claude further edit\n", encoding="utf-8")

    revert_wiki(wiki, preserve=frozenset({(wiki / "index.md").resolve()}))

    assert (wiki / "index.md").read_text(encoding="utf-8") == "claude further edit\n"


def test_stage_and_commit_paths_raises_on_git_failure(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    (not_a_repo / "log.md").write_text("# log\n", encoding="utf-8")
    with pytest.raises(WikiCommitError):
        stage_and_commit_paths(
            wiki_dir=not_a_repo,
            paths=[not_a_repo / "log.md"],
            message="boom",
        )


def test_stage_and_commit_paths_returns_sha_on_change(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "log.md").write_text("# log\nappended\n", encoding="utf-8")

    sha = stage_and_commit_paths(
        wiki_dir=wiki,
        paths=[wiki / "log.md"],
        message="log only",
    )

    assert sha is not None
    assert len(sha) == 40
    head = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head == sha


def test_stage_and_commit_paths_does_not_stage_other_dirty_files(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "index.md").write_text("# index modified\n", encoding="utf-8")
    (wiki / "log.md").write_text("# log\nentry\n", encoding="utf-8")

    sha = stage_and_commit_paths(
        wiki_dir=wiki,
        paths=[wiki / "log.md"],
        message="log only",
    )

    assert sha is not None
    files = _git("show", "--name-only", "--pretty=", "HEAD", cwd=wiki).stdout.split()
    assert files == ["log.md"]
    status = _git("status", "--porcelain", cwd=wiki).stdout.strip()
    assert "index.md" in status
    assert "log.md" not in status


def test_failure_commit_includes_only_log_md(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / ".gitignore").write_text(".DS_Store\n", encoding="utf-8")
    (wiki / "CLAUDE.md").write_text("# user notes\n", encoding="utf-8")

    append_log_failure(
        wiki_dir=wiki,
        repo_name="proj-a",
        short_sha="abc1234",
        subject="something broke",
        error="boom traceback",
        today_iso="2026-05-15",
    )

    files = _git("show", "--name-only", "--pretty=", "HEAD", cwd=wiki).stdout.split()
    assert files == ["log.md"]
    status = _git("status", "--porcelain", cwd=wiki).stdout
    assert ".gitignore" in status
    assert "CLAUDE.md" in status


def test_failure_commit_returns_none_when_log_unchanged(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()

    result = stage_and_commit_paths(
        wiki_dir=wiki,
        paths=[wiki / "log.md"],
        message="noop",
    )

    assert result is None
    head_after = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head_before == head_after


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
