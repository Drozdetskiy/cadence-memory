"""Tests for the `cadence-memory hooks` CLI sub-app."""

from __future__ import annotations

import stat
import subprocess
from importlib.resources import files
from pathlib import Path

from typer.testing import CliRunner

from cadence_memory.cli import app


def _setup_wiki(path: Path) -> None:
    """Create a minimal wiki dir: config.yaml + git repo."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    (path / "config.yaml").write_text("repos: []\n", encoding="utf-8")


def _template_bytes() -> bytes:
    defaults = files("cadence_memory.defaults")
    return (defaults / "wiki" / "git-hooks" / "post-commit").read_bytes()


def test_cli_hooks_install_exit_0_on_fresh_repo(tmp_path: Path) -> None:
    _setup_wiki(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["hooks", "install", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "installed:" in result.stdout
    hook = tmp_path / ".git" / "hooks" / "post-commit"
    assert hook.is_file()
    assert hook.stat().st_mode & stat.S_IXUSR


def test_cli_hooks_install_idempotent(tmp_path: Path) -> None:
    _setup_wiki(tmp_path)
    runner = CliRunner()

    first = runner.invoke(app, ["hooks", "install", "--wiki", str(tmp_path)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["hooks", "install", "--wiki", str(tmp_path)])
    assert second.exit_code == 0, second.output
    assert "already installed:" in second.stdout


def test_cli_hooks_install_refuses_foreign(tmp_path: Path) -> None:
    _setup_wiki(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-commit"
    hook.write_text("#!/bin/bash\necho foreign\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["hooks", "install", "--wiki", str(tmp_path)])

    assert result.exit_code == 2
    assert "--force" in result.stderr
    assert hook.read_text(encoding="utf-8") == "#!/bin/bash\necho foreign\n"


def test_cli_hooks_install_force_overwrites(tmp_path: Path) -> None:
    _setup_wiki(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-commit"
    hook.write_text("#!/bin/bash\necho foreign\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["hooks", "install", "--force", "--wiki", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "installed:" in result.stdout
    assert hook.read_bytes() == _template_bytes()


def test_cli_hooks_install_no_git_dir(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("repos: []\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["hooks", "install", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert "no .git directory" in result.stderr
    assert "Traceback" not in result.output


def test_cli_hooks_install_wiki_not_found(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["hooks", "install", "--wiki", str(tmp_path)])

    assert result.exit_code == 1
    assert "config.yaml" in result.stderr
    assert "Traceback" not in result.output
