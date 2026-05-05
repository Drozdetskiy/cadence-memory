"""End-to-end tests for the `cadence-memory discover` CLI command."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory import cli
from cadence_memory.cli import app
from cadence_memory.config import load_annotations_config
from cadence_memory.discover.runner import (
    DiscoverFailed,
    DiscoverInputs,
    DiscoverInvalidOutput,
    DiscoverTimedOut,
)
from cadence_memory.executor.claude_executor import ClaudeNotFound, ClaudeRunner

runner = CliRunner()


def _make_store(
    tmp_path: Path,
    *,
    projects: Iterable[str] = ("alpha",),
) -> Path:
    """Create a store dir + project dirs, each with a sample README.md."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()

    lines = ["projects:"]
    for name in projects:
        project_dir = tmp_path / name
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Title\n\nbody\n", encoding="utf-8")
        lines.append(f"  - name: {name}")
        lines.append(f"    path: {project_dir}")
    lines.append("defaults:")
    lines.append("  kind: doc")
    (store_dir / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return store_dir


def _valid_yaml(project: str = "alpha") -> str:
    return f"documents:\n  - id: {project}:README.md\n    project: {project}\n    path: README.md\n"


class _FakeRunner:
    """Sentinel object stand-in for a real ClaudeRunner."""


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_run: Callable[[DiscoverInputs, ClaudeRunner], None] | None = None,
    raises: BaseException | None = None,
) -> dict[str, Any]:
    captured: dict[str, Any] = {"runs": []}

    def fake_factory(idle_timeout: float) -> ClaudeRunner:
        captured["idle_timeout"] = idle_timeout
        fake = _FakeRunner()
        captured["runner"] = fake
        return fake

    def fake_run(inputs: DiscoverInputs, runner_obj: ClaudeRunner) -> None:
        captured["runs"].append((inputs, runner_obj))
        if on_run is not None:
            on_run(inputs, runner_obj)
        if raises is not None:
            raise raises

    monkeypatch.setattr(cli, "_discover_runner_factory", fake_factory)
    monkeypatch.setattr(cli, "_discover_run_func", fake_run)
    return captured


def _install_dirty_check(monkeypatch: pytest.MonkeyPatch, *, dirty: bool) -> dict[str, Any]:
    captured: dict[str, Any] = {"calls": []}

    def fake_check(store_dir: Path, name: str) -> bool:
        captured["calls"].append((store_dir, name))
        return dirty

    monkeypatch.setattr(cli, "_git_dirty_check", fake_check)
    return captured


def _drop_yaml(text: str) -> Callable[[DiscoverInputs, ClaudeRunner], None]:
    def write(inputs: DiscoverInputs, _runner: ClaudeRunner) -> None:
        inputs.output_path.write_text(text, encoding="utf-8")

    return write


def test_unknown_project_exits_1_with_known_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path, projects=("alpha", "beta"))
    captured = _install_stubs(monkeypatch)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "discover", "--project", "nope"],
    )

    assert result.exit_code == 1
    assert "error: unknown project: nope" in result.stderr
    assert "known projects: alpha, beta" in result.stderr
    assert captured["runs"] == []


def test_default_writes_proposed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path)
    _install_stubs(monkeypatch, on_run=_drop_yaml(_valid_yaml()))

    result = runner.invoke(app, ["--store", str(store_dir), "discover"])

    assert result.exit_code == 0, result.output + result.stderr
    proposed = store_dir / "annotations-config.yaml.proposed"
    assert proposed.exists()
    assert f"wrote: {proposed}" in result.stdout
    assert "next: review the diff, then re-run with --apply" in result.stdout
    assert f"-> {proposed}" in result.stderr
    # The dropped YAML must be valid annotations-config content.
    load_annotations_config(proposed, config=None)


def test_apply_writes_real_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path)
    _install_dirty_check(monkeypatch, dirty=False)
    _install_stubs(monkeypatch, on_run=_drop_yaml(_valid_yaml()))

    result = runner.invoke(app, ["--store", str(store_dir), "discover", "--apply"])

    assert result.exit_code == 0, result.output + result.stderr
    real = store_dir / "annotations-config.yaml"
    assert real.exists()
    assert f"wrote: {real}" in result.stdout
    assert "next:" not in result.stdout


def test_apply_refuses_when_dirty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path)
    _install_dirty_check(monkeypatch, dirty=True)
    captured = _install_stubs(monkeypatch)

    result = runner.invoke(app, ["--store", str(store_dir), "discover", "--apply"])

    assert result.exit_code == 1
    assert (
        "error: annotations-config.yaml has uncommitted changes; "
        "commit or stash before re-running with --apply"
    ) in result.stderr
    assert captured["runs"] == []


def test_apply_proceeds_when_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path)
    dirty_calls = _install_dirty_check(monkeypatch, dirty=False)
    run_calls = _install_stubs(monkeypatch, on_run=_drop_yaml(_valid_yaml()))

    result = runner.invoke(app, ["--store", str(store_dir), "discover", "--apply"])

    assert result.exit_code == 0, result.output + result.stderr
    assert dirty_calls["calls"] == [(store_dir, "annotations-config.yaml")]
    assert len(run_calls["runs"]) == 1


def test_multi_project_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path, projects=("alpha", "beta"))
    captured = _install_stubs(monkeypatch, on_run=_drop_yaml(_valid_yaml("alpha")))

    real_scan = cli.scan_project
    scan_calls: list[str] = []

    def spy_scan(project: Any) -> Any:
        scan_calls.append(project.name)
        return real_scan(project)

    monkeypatch.setattr(cli, "scan_project", spy_scan)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "discover",
            "--project",
            "alpha",
            "--project",
            "beta",
        ],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert scan_calls == ["alpha", "beta"]
    inputs, _runner = captured["runs"][0]
    assert tuple(t.project.name for t in inputs.targets) == ("alpha", "beta")


def test_existing_proposed_overwritten_with_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path)
    proposed = store_dir / "annotations-config.yaml.proposed"
    proposed.write_text("documents: []\n", encoding="utf-8")
    new_yaml = _valid_yaml()
    _install_stubs(monkeypatch, on_run=_drop_yaml(new_yaml))

    result = runner.invoke(app, ["--store", str(store_dir), "discover"])

    assert result.exit_code == 0, result.output + result.stderr
    assert "warn: overwriting existing annotations-config.yaml.proposed" in result.stderr
    assert proposed.read_text(encoding="utf-8") == new_yaml


@pytest.mark.parametrize(
    ("exception", "expected_code", "expected_substring"),
    [
        (DiscoverFailed(exit_code=42), 2, "discover failed: claude exited with code 42"),
        (DiscoverTimedOut(), 4, "discover failed: idle timeout"),
        (
            DiscoverInvalidOutput(Path("/tmp/anno.yaml"), "missing documents key"),
            3,
            "discover failed: invalid output at /tmp/anno.yaml: missing documents key",
        ),
        (ClaudeNotFound(), 127, "discover failed: claude not found on PATH"),
    ],
)
def test_exit_codes_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
    expected_code: int,
    expected_substring: str,
) -> None:
    store_dir = _make_store(tmp_path)
    _install_stubs(monkeypatch, raises=exception)

    result = runner.invoke(app, ["--store", str(store_dir), "discover"])

    assert result.exit_code == expected_code
    assert expected_substring in result.stderr


def test_idle_timeout_propagates_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path)
    captured = _install_stubs(monkeypatch, on_run=_drop_yaml(_valid_yaml()))

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "discover", "--idle-timeout", "0"],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert captured["idle_timeout"] == 0.0


def test_idle_timeout_default_is_300(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path)
    captured = _install_stubs(monkeypatch, on_run=_drop_yaml(_valid_yaml()))

    result = runner.invoke(app, ["--store", str(store_dir), "discover"])

    assert result.exit_code == 0, result.output + result.stderr
    assert captured["idle_timeout"] == 300.0


def test_empty_projects_config_exits_with_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "config.yaml").write_text("projects: []\n", encoding="utf-8")
    captured = _install_stubs(monkeypatch)

    result = runner.invoke(app, ["--store", str(store_dir), "discover"])

    assert result.exit_code == 1
    assert "error: no projects configured" in result.stderr
    assert captured["runs"] == []


def test_default_git_dirty_check_returns_true_on_dirty_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, " M annotations-config.yaml\n", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._default_git_dirty_check(tmp_path, "annotations-config.yaml") is True


def test_default_git_dirty_check_returns_false_on_clean_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._default_git_dirty_check(tmp_path, "annotations-config.yaml") is False


def test_default_git_dirty_check_returns_false_when_git_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._default_git_dirty_check(tmp_path, "annotations-config.yaml") is False


def test_default_git_dirty_check_returns_false_on_nonzero_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: not a git repository")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._default_git_dirty_check(tmp_path, "annotations-config.yaml") is False
