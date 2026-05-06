"""End-to-end tests for the `cadence-memory discover` CLI command."""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
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
from cadence_memory.executor.claude_executor import (
    ClaudeNotFound,
    ClaudeRunner,
    RunResult,
)

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


_ALL_CACHE_RECORDS: list[dict[str, object]] = [
    {
        "id": "alpha:README.md",
        "path": "README.md",
        "project": "alpha",
        "kind": "doc",
        "title": "Alpha Readme",
    },
    {
        "id": "alpha:docs/guide.md",
        "path": "docs/guide.md",
        "project": "alpha",
        "kind": "doc",
        "title": "Guide",
    },
    {
        "id": "beta:notes.md",
        "path": "notes.md",
        "project": "beta",
        "kind": "doc",
        "title": "Notes",
    },
]


def _make_cache_store(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    store_dir = tmp_path / "store"
    store_dir.mkdir()

    files: dict[str, Path] = {}

    alpha_dir = tmp_path / "alpha"
    (alpha_dir / "docs").mkdir(parents=True)
    readme = alpha_dir / "README.md"
    readme.write_text("# Alpha Readme\n\nbody\n", encoding="utf-8")
    files["alpha:README.md"] = readme
    guide = alpha_dir / "docs" / "guide.md"
    guide.write_text("# Guide\n\nmore body\n", encoding="utf-8")
    files["alpha:docs/guide.md"] = guide

    beta_dir = tmp_path / "beta"
    beta_dir.mkdir()
    notes = beta_dir / "notes.md"
    notes.write_text("# Notes\n\nbeta body\n", encoding="utf-8")
    files["beta:notes.md"] = notes

    config = (
        "projects:\n"
        f"  - name: alpha\n    path: {alpha_dir}\n"
        f"  - name: beta\n    path: {beta_dir}\n"
        "defaults:\n  kind: doc\n"
    )
    (store_dir / "config.yaml").write_text(config, encoding="utf-8")
    return store_dir, files


def _files_in_prompt(prompt: str) -> set[tuple[str, str]]:
    start_marker = "Projects and files to annotate:\n\n"
    end_marker = "\n\nWrite only the YAML"
    start = prompt.find(start_marker)
    end = prompt.find(end_marker)
    block = prompt[start + len(start_marker) : end] if start >= 0 and end > 0 else ""
    parsed = yaml.safe_load(block)
    seen: set[tuple[str, str]] = set()
    if isinstance(parsed, list):
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "")
            if not isinstance(name, str):
                continue
            for rel in entry.get("files", []) or []:
                if isinstance(rel, str):
                    seen.add((name, rel))
    return seen


def _records_in_prompt(prompt: str) -> list[dict[str, object]]:
    files = _files_in_prompt(prompt)
    return [r for r in _ALL_CACHE_RECORDS if (r["project"], r["path"]) in files]


class _CaptureRunner:
    def __init__(
        self,
        *,
        output_path: Path,
        records_for: Callable[[str], list[dict[str, object]]],
    ) -> None:
        self._output_path = output_path
        self._records_for = records_for
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def run(
        self,
        prompt: str,
        *,
        env: Mapping[str, str] | None = None,
    ) -> RunResult:
        captured_env: Mapping[str, str] = env if env is not None else {}
        self.calls.append((prompt, captured_env))
        records = self._records_for(prompt)
        self._output_path.write_text(
            yaml.safe_dump({"documents": records}, sort_keys=False),
            encoding="utf-8",
        )
        return RunResult(output="", exit_code=0, idle_timed_out=False)


def _install_runner_only(
    monkeypatch: pytest.MonkeyPatch, runner_obj: ClaudeRunner
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def factory(idle_timeout: float) -> ClaudeRunner:
        captured["idle_timeout"] = idle_timeout
        return runner_obj

    monkeypatch.setattr(cli, "_discover_runner_factory", factory)
    return captured


def _cache_count(store_dir: Path) -> int:
    db = sqlite3.connect(str(store_dir / "index.sqlite"))
    try:
        row = db.execute("SELECT COUNT(*) FROM discover_cache").fetchone()
    finally:
        db.close()
    return int(row[0])


def test_discover_help_lists_no_cache_flag(tmp_path: Path) -> None:
    result = runner.invoke(app, ["discover", "--help"])
    assert result.exit_code == 0
    assert "--no-cache" in result.stdout


def test_first_run_populates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir, _files = _make_cache_store(tmp_path)
    proposed = store_dir / "annotations-config.yaml.proposed"
    capture = _CaptureRunner(output_path=proposed, records_for=_records_in_prompt)
    _install_runner_only(monkeypatch, capture)

    result = runner.invoke(app, ["--store", str(store_dir), "discover"])

    assert result.exit_code == 0, result.output + result.stderr
    assert len(capture.calls) == 1
    assert _cache_count(store_dir) == len(_ALL_CACHE_RECORDS)


def test_second_run_skips_runner_with_full_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir, _files = _make_cache_store(tmp_path)
    proposed = store_dir / "annotations-config.yaml.proposed"
    capture = _CaptureRunner(output_path=proposed, records_for=_records_in_prompt)
    _install_runner_only(monkeypatch, capture)

    result1 = runner.invoke(app, ["--store", str(store_dir), "discover"])
    assert result1.exit_code == 0, result1.output + result1.stderr
    first_records = yaml.safe_load(proposed.read_text(encoding="utf-8"))["documents"]
    proposed.unlink()

    result2 = runner.invoke(app, ["--store", str(store_dir), "discover"])

    assert result2.exit_code == 0, result2.output + result2.stderr
    assert len(capture.calls) == 1
    second_records = yaml.safe_load(proposed.read_text(encoding="utf-8"))["documents"]
    assert sorted(first_records, key=lambda r: r["id"]) == sorted(
        second_records, key=lambda r: r["id"]
    )


def test_partial_cache_only_changed_file_in_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir, files = _make_cache_store(tmp_path)
    proposed = store_dir / "annotations-config.yaml.proposed"
    capture = _CaptureRunner(output_path=proposed, records_for=_records_in_prompt)
    _install_runner_only(monkeypatch, capture)

    result1 = runner.invoke(app, ["--store", str(store_dir), "discover"])
    assert result1.exit_code == 0, result1.output + result1.stderr
    assert len(capture.calls) == 1

    files["alpha:docs/guide.md"].write_text("# Edited\n\nnew body\n", encoding="utf-8")

    result2 = runner.invoke(app, ["--store", str(store_dir), "discover"])

    assert result2.exit_code == 0, result2.output + result2.stderr
    assert len(capture.calls) == 2
    assert _files_in_prompt(capture.calls[1][0]) == {("alpha", "docs/guide.md")}

    final_docs = yaml.safe_load(proposed.read_text(encoding="utf-8"))["documents"]
    assert {(d["project"], d["path"]) for d in final_docs} == {
        ("alpha", "README.md"),
        ("alpha", "docs/guide.md"),
        ("beta", "notes.md"),
    }


def test_no_cache_flag_invokes_runner_with_all_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir, _files = _make_cache_store(tmp_path)
    proposed = store_dir / "annotations-config.yaml.proposed"
    capture = _CaptureRunner(output_path=proposed, records_for=_records_in_prompt)
    _install_runner_only(monkeypatch, capture)

    result1 = runner.invoke(app, ["--store", str(store_dir), "discover"])
    assert result1.exit_code == 0, result1.output + result1.stderr
    assert len(capture.calls) == 1

    result2 = runner.invoke(app, ["--store", str(store_dir), "discover", "--no-cache"])

    assert result2.exit_code == 0, result2.output + result2.stderr
    assert len(capture.calls) == 2
    assert _files_in_prompt(capture.calls[1][0]) == {
        ("alpha", "README.md"),
        ("alpha", "docs/guide.md"),
        ("beta", "notes.md"),
    }
    assert _cache_count(store_dir) == len(_ALL_CACHE_RECORDS)


def test_no_cache_flag_populates_empty_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir, _files = _make_cache_store(tmp_path)
    proposed = store_dir / "annotations-config.yaml.proposed"
    capture = _CaptureRunner(output_path=proposed, records_for=_records_in_prompt)
    _install_runner_only(monkeypatch, capture)

    result = runner.invoke(app, ["--store", str(store_dir), "discover", "--no-cache"])

    assert result.exit_code == 0, result.output + result.stderr
    assert len(capture.calls) == 1
    assert _cache_count(store_dir) == len(_ALL_CACHE_RECORDS)


def test_discover_cache_isolated_from_documents_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir, _files = _make_cache_store(tmp_path)
    proposed = store_dir / "annotations-config.yaml.proposed"
    capture = _CaptureRunner(output_path=proposed, records_for=_records_in_prompt)
    _install_runner_only(monkeypatch, capture)

    result = runner.invoke(app, ["--store", str(store_dir), "discover"])
    assert result.exit_code == 0, result.output + result.stderr

    db = sqlite3.connect(str(store_dir / "index.sqlite"))
    try:
        cache_count = db.execute("SELECT COUNT(*) FROM discover_cache").fetchone()[0]
        doc_ids = db.execute("SELECT id FROM documents").fetchall()
    finally:
        db.close()
    assert cache_count == len(_ALL_CACHE_RECORDS)
    assert doc_ids == []
