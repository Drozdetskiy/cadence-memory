"""Tests for the local git cache (design2 §6.1 step 1)."""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from cadence_memory.git import (
    CloneResult,
    CommitInfo,
    DefaultGitCache,
    GitError,
    HistoryRewrittenError,
)
from cadence_memory.git.cache import _parse_log_records


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "HOME": str(cwd),
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )
    return result.stdout


def _make_local_remote(tmp_path: Path, *, name: str, commits: list[str]) -> Path:
    """Create a non-bare git repo on `main` with the given commit subjects.

    Returns the absolute path; callers turn it into a `file://` URL.
    """
    remote = tmp_path / name
    remote.mkdir()
    _run_git(remote, "init", "--initial-branch=main")
    for i, subject in enumerate(commits):
        (remote / f"file_{i}.txt").write_text(f"content {i}\n")
        _run_git(remote, "add", ".")
        _run_git(remote, "commit", "-m", subject)
    return remote


def test_dtos_are_frozen_and_error_hierarchy(tmp_path: Path) -> None:
    clone = CloneResult(path=tmp_path, head_sha="a" * 40, was_initial_clone=True)
    commit = CommitInfo(
        sha="a" * 40,
        short_sha="aaaaaaa",
        subject="Initial commit",
        body="",
        author_date_iso="2026-05-12T00:00:00+00:00",
        parents=(),
    )

    assert clone.head_sha == "a" * 40
    assert commit.parents == ()

    with pytest.raises((FrozenInstanceError, AttributeError)):
        clone.head_sha = "b" * 40  # type: ignore[misc]
    with pytest.raises((FrozenInstanceError, AttributeError)):
        commit.subject = "changed"  # type: ignore[misc]

    assert issubclass(HistoryRewrittenError, GitError)


def test_run_wraps_nonzero_exit_in_giterror(tmp_path: Path) -> None:
    (tmp_path / "nope").mkdir()
    cache = DefaultGitCache(root=tmp_path)
    with pytest.raises(GitError) as exc_info:
        cache.head(name="nope", branch="main")
    message = str(exc_info.value)
    assert "git" in message
    assert "failed" in message


def test_clone_creates_directory(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["Initial"])
    upstream_head = _run_git(remote, "rev-parse", "HEAD").strip()

    cache_root = tmp_path / "cache"
    cache = DefaultGitCache(root=cache_root)
    result = cache.ensure(name="repo", url=f"file://{remote}", branch="main")

    assert isinstance(result, CloneResult)
    assert result.was_initial_clone is True
    assert result.path == cache_root / "repo"
    assert result.path.is_dir()
    assert (result.path / ".git").is_dir()
    assert result.head_sha == upstream_head


def test_fetch_updates_existing(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["Initial"])
    first_head = _run_git(remote, "rev-parse", "HEAD").strip()

    cache = DefaultGitCache(root=tmp_path / "cache")
    url = f"file://{remote}"
    first = cache.ensure(name="repo", url=url, branch="main")
    assert first.was_initial_clone is True
    assert first.head_sha == first_head

    (remote / "second.txt").write_text("more\n")
    _run_git(remote, "add", ".")
    _run_git(remote, "commit", "-m", "Second")
    second_head = _run_git(remote, "rev-parse", "HEAD").strip()
    assert second_head != first_head

    second = cache.ensure(name="repo", url=url, branch="main")
    assert second.was_initial_clone is False
    assert second.head_sha == second_head


def test_url_mismatch_errors(tmp_path: Path) -> None:
    remote_a = _make_local_remote(tmp_path, name="remote_a", commits=["A"])
    remote_b = _make_local_remote(tmp_path, name="remote_b", commits=["B"])
    url_a = f"file://{remote_a}"
    url_b = f"file://{remote_b}"

    cache = DefaultGitCache(root=tmp_path / "cache")
    cache.ensure(name="repo", url=url_a, branch="main")

    with pytest.raises(GitError) as exc_info:
        cache.ensure(name="repo", url=url_b, branch="main")
    message = str(exc_info.value)
    assert url_a in message
    assert url_b in message


def test_list_commits_empty_when_caught_up(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["Only"])
    cache = DefaultGitCache(root=tmp_path / "cache")
    result = cache.ensure(name="repo", url=f"file://{remote}", branch="main")

    commits = cache.list_commits(
        name="repo",
        since_sha=result.head_sha,
        branch="main",
    )
    assert commits == ()


def test_list_commits_topo_order(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["A", "B", "C"])
    cache = DefaultGitCache(root=tmp_path / "cache")
    cache.ensure(name="repo", url=f"file://{remote}", branch="main")

    commits = cache.list_commits(
        name="repo",
        since_sha=None,
        branch="main",
        reverse=True,
    )
    subjects = [c.subject for c in commits]
    assert subjects == ["A", "B", "C"]
    assert commits[0].parents == ()
    assert commits[1].parents == (commits[0].sha,)
    assert commits[2].parents == (commits[1].sha,)
    for commit in commits:
        assert len(commit.sha) == 40
        assert len(commit.short_sha) >= 7
        assert "T" in commit.author_date_iso


def test_list_commits_handles_multiline_subject(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _run_git(remote, "init", "--initial-branch=main")
    (remote / "file.txt").write_text("hi\n")
    _run_git(remote, "add", ".")
    _run_git(
        remote,
        "commit",
        "-m",
        "Subject line",
        "-m",
        "Body opening paragraph.",
        "-m",
        "---\nAfter separator.",
    )

    cache = DefaultGitCache(root=tmp_path / "cache")
    cache.ensure(name="repo", url=f"file://{remote}", branch="main")

    commits = cache.list_commits(name="repo", since_sha=None, branch="main")
    assert len(commits) == 1
    only = commits[0]
    assert only.subject == "Subject line"
    assert "\n" not in only.subject
    assert "Body opening paragraph." in only.body
    assert "\n\n---\nAfter separator." in only.body


def test_history_rewritten_detected(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["Original"])
    original_head = _run_git(remote, "rev-parse", "HEAD").strip()

    cache = DefaultGitCache(root=tmp_path / "cache")
    url = f"file://{remote}"
    first = cache.ensure(name="repo", url=url, branch="main")
    assert first.head_sha == original_head

    _run_git(remote, "commit", "--amend", "--allow-empty", "-m", "Amended")
    new_head = _run_git(remote, "rev-parse", "HEAD").strip()
    assert new_head != original_head

    second = cache.ensure(name="repo", url=url, branch="main")
    assert second.head_sha == new_head

    with pytest.raises(HistoryRewrittenError) as exc_info:
        cache.list_commits(name="repo", since_sha=original_head, branch="main")
    assert original_head in str(exc_info.value)


def test_show_commit_returns_single_info(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["Initial"])
    head_sha = _run_git(remote, "rev-parse", "HEAD").strip()
    short_sha = _run_git(remote, "rev-parse", "--short", head_sha).strip()

    cache = DefaultGitCache(root=tmp_path / "cache")
    cache.ensure(name="repo", url=f"file://{remote}", branch="main")

    info = cache.show_commit(name="repo", sha=head_sha)
    assert isinstance(info, CommitInfo)
    assert info.sha == head_sha
    assert info.short_sha == short_sha
    assert info.subject == "Initial"
    assert info.body == ""
    assert info.parents == ()
    assert "T" in info.author_date_iso


def test_diff_includes_added_lines(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["Initial"])
    (remote / "foo.py").write_text("MAGIC_LINE_FOR_TEST\n")
    _run_git(remote, "add", ".")
    _run_git(remote, "commit", "-m", "Add foo")
    head_sha = _run_git(remote, "rev-parse", "HEAD").strip()

    cache = DefaultGitCache(root=tmp_path / "cache")
    cache.ensure(name="repo", url=f"file://{remote}", branch="main")

    diff_output = cache.diff(name="repo", sha=head_sha)
    assert "+MAGIC_LINE_FOR_TEST" in diff_output
    assert "foo.py" in diff_output


def test_changed_files(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["Initial"])
    (remote / "foo.py").write_text("foo\n")
    (remote / "bar.py").write_text("bar\n")
    _run_git(remote, "add", ".")
    _run_git(remote, "commit", "-m", "Add foo and bar")
    head_sha = _run_git(remote, "rev-parse", "HEAD").strip()

    cache = DefaultGitCache(root=tmp_path / "cache")
    cache.ensure(name="repo", url=f"file://{remote}", branch="main")

    files = cache.changed_files(name="repo", sha=head_sha)
    assert set(files) == {"foo.py", "bar.py"}
    assert len(files) == 2


def test_parse_log_records_rejects_bad_field_count() -> None:
    with pytest.raises(GitError) as exc_info:
        _parse_log_records("a\x00b\x00c\x1e")
    assert "6 fields" in str(exc_info.value)


def test_root_commit_diff(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path, name="remote", commits=["Root"])
    root_sha = _run_git(remote, "rev-parse", "HEAD").strip()

    cache = DefaultGitCache(root=tmp_path / "cache")
    cache.ensure(name="repo", url=f"file://{remote}", branch="main")

    info = cache.show_commit(name="repo", sha=root_sha)
    assert info.parents == ()

    diff_output = cache.diff(name="repo", sha=root_sha)
    assert "file_0.txt" in diff_output
    assert "+content 0" in diff_output
