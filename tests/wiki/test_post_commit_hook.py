"""Tests for `install_post_commit_hook` (design2 §10, §13)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path

import pytest

from cadence_memory.wiki import HookInstallError, InstallOutcome, install_post_commit_hook


def _make_git_repo(path: Path) -> Path:
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path


def test_install_creates_hook_with_exec_bit(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    outcome = install_post_commit_hook(repo)
    assert outcome == InstallOutcome.INSTALLED
    hook = repo / ".git" / "hooks" / "post-commit"
    assert hook.is_file()
    assert hook.stat().st_mode & stat.S_IXUSR


def test_install_idempotent(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    install_post_commit_hook(repo)
    hook = repo / ".git" / "hooks" / "post-commit"
    bytes_before = hook.read_bytes()
    mtime_before = hook.stat().st_mtime_ns

    outcome = install_post_commit_hook(repo)

    assert outcome == InstallOutcome.ALREADY_PRESENT
    assert hook.read_bytes() == bytes_before
    assert hook.stat().st_mtime_ns == mtime_before


def test_install_foreign_content_not_overwritten(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-commit"
    hook.write_bytes(b"#!/bin/sh\necho custom\n")
    foreign_bytes = hook.read_bytes()

    outcome = install_post_commit_hook(repo)

    assert outcome == InstallOutcome.FOREIGN_PRESENT
    assert hook.read_bytes() == foreign_bytes


def test_install_force_overwrites_foreign(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-commit"
    hook.write_bytes(b"#!/bin/sh\necho custom\n")

    outcome = install_post_commit_hook(repo, force=True)

    assert outcome == InstallOutcome.INSTALLED
    defaults = files("cadence_memory.defaults")
    template_bytes = (defaults / "wiki" / "git-hooks" / "post-commit").read_bytes()
    assert hook.read_bytes() == template_bytes


def test_install_raises_when_no_git_dir(tmp_path: Path) -> None:
    with pytest.raises(HookInstallError, match=r"no \.git directory"):
        install_post_commit_hook(tmp_path)


def test_install_force_noop_when_bytes_match(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    install_post_commit_hook(repo)
    hook = repo / ".git" / "hooks" / "post-commit"
    mtime_before = hook.stat().st_mtime_ns

    outcome = install_post_commit_hook(repo, force=True)

    assert outcome == InstallOutcome.ALREADY_PRESENT
    assert hook.stat().st_mtime_ns == mtime_before


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
def test_hook_no_qmd_runs_cleanly(tmp_path: Path) -> None:
    bash_exe = shutil.which("bash") or "/bin/bash"
    repo = _make_git_repo(tmp_path / "repo")
    install_post_commit_hook(repo)
    hook = repo / ".git" / "hooks" / "post-commit"

    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    result = subprocess.run(
        [bash_exe, str(hook)],
        capture_output=True,
        env={**os.environ, "PATH": str(empty_bin)},
        cwd=str(repo),
    )

    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
def test_hook_with_fake_qmd_invokes(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    install_post_commit_hook(repo)
    hook = repo / ".git" / "hooks" / "post-commit"

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    marker = tmp_path / "argv.txt"
    qmd = shim_dir / "qmd"
    qmd.write_text(f'#!/usr/bin/env bash\necho "$@" > "{marker}"\n')
    qmd.chmod(0o755)

    env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(hook)],
        capture_output=True,
        env=env,
        cwd=str(repo),
    )

    assert result.returncode == 0

    deadline = time.monotonic() + 5.0
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)

    assert marker.exists(), "fake qmd was never invoked"
    tokens = marker.read_text().strip().split()
    assert tokens == ["index", str(repo.resolve()), "--collection", "master"]


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
def test_hook_failure_does_not_block_commit(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    install_post_commit_hook(repo)
    hook = repo / ".git" / "hooks" / "post-commit"

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    qmd = shim_dir / "qmd"
    qmd.write_text("#!/usr/bin/env bash\nexit 1\n")
    qmd.chmod(0o755)

    env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(hook)],
        capture_output=True,
        env=env,
        cwd=str(repo),
    )

    assert result.returncode == 0
