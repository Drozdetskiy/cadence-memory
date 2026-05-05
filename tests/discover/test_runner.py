"""Unit tests for the discover orchestrator."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cadence_memory.config import (
    DiscoverConfig,
    KindRule,
    ProjectConfig,
)
from cadence_memory.discover.runner import (
    DiscoverFailed,
    DiscoverInputs,
    DiscoverInvalidOutput,
    DiscoverTarget,
    DiscoverTimedOut,
    run_discover,
)
from cadence_memory.discover.scanner import DiscoveredFile
from cadence_memory.executor.claude_executor import RunResult


@dataclass
class _StubRunner:
    on_run: Callable[[str, Mapping[str, str], Path], None]
    result: RunResult
    output_path: Path
    last_prompt: str | None = field(default=None)
    last_env: Mapping[str, str] | None = field(default=None)

    def run(
        self,
        prompt: str,
        *,
        env: Mapping[str, str] | None = None,
    ) -> RunResult:
        captured_env: Mapping[str, str] = env if env is not None else {}
        self.last_prompt = prompt
        self.last_env = captured_env
        self.on_run(prompt, captured_env, self.output_path)
        return self.result


def _make_inputs(
    tmp_path: Path,
    *,
    output_path: Path,
    projects: tuple[tuple[str, tuple[KindRule, ...], tuple[str, ...]], ...] = (
        ("alpha", (KindRule(pattern="docs/adr/*.md", kind="adr"),), ("README.md", "docs/guide.md")),
        ("beta", (), ("notes.md",)),
    ),
) -> DiscoverInputs:
    targets: list[DiscoverTarget] = []
    for name, kind_rules, rel_paths in projects:
        project_root = tmp_path / "projects" / name
        project_root.mkdir(parents=True, exist_ok=True)
        project = ProjectConfig(
            name=name,
            path=project_root,
            exclude=(),
            discover=DiscoverConfig(kind_rules=kind_rules),
        )
        files = tuple(
            DiscoveredFile(
                project=name,
                abs_path=project_root / rel,
                rel_path=rel,
            )
            for rel in rel_paths
        )
        targets.append(DiscoverTarget(project=project, files=files))
    store_dir = tmp_path / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    return DiscoverInputs(
        store_dir=store_dir,
        targets=tuple(targets),
        output_path=output_path,
    )


def _write_valid_yaml(path: Path) -> None:
    path.write_text(
        "documents:\n"
        "  - id: alpha:README.md\n"
        "    path: README.md\n"
        "    kind: doc\n"
        "    title: Readme\n"
        "    tags: [readme]\n",
        encoding="utf-8",
    )


def test_run_discover_happy_path(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    inputs = _make_inputs(tmp_path, output_path=output_path)

    def writer(prompt: str, env: Mapping[str, str], path: Path) -> None:
        _write_valid_yaml(path)

    runner = _StubRunner(
        on_run=writer,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    run_discover(inputs, runner=runner)
    assert output_path.exists()


def test_run_discover_non_zero_exit_raises_failed(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    inputs = _make_inputs(tmp_path, output_path=output_path)

    runner = _StubRunner(
        on_run=lambda prompt, env, path: None,
        result=RunResult(output="", exit_code=1, idle_timed_out=False),
        output_path=output_path,
    )

    with pytest.raises(DiscoverFailed) as excinfo:
        run_discover(inputs, runner=runner)
    assert excinfo.value.exit_code == 1


def test_run_discover_idle_timeout_raises_timed_out(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    inputs = _make_inputs(tmp_path, output_path=output_path)

    runner = _StubRunner(
        on_run=lambda prompt, env, path: None,
        result=RunResult(output="", exit_code=137, idle_timed_out=True),
        output_path=output_path,
    )

    with pytest.raises(DiscoverTimedOut):
        run_discover(inputs, runner=runner)


def test_run_discover_missing_output_raises_invalid(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    inputs = _make_inputs(tmp_path, output_path=output_path)

    runner = _StubRunner(
        on_run=lambda prompt, env, path: None,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    with pytest.raises(DiscoverInvalidOutput) as excinfo:
        run_discover(inputs, runner=runner)
    assert "did not produce" in str(excinfo.value)


def test_run_discover_invalid_yaml_preserves_file(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    inputs = _make_inputs(tmp_path, output_path=output_path)

    def writer(prompt: str, env: Mapping[str, str], path: Path) -> None:
        path.write_text("not_documents: []\n", encoding="utf-8")

    runner = _StubRunner(
        on_run=writer,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    with pytest.raises(DiscoverInvalidOutput):
        run_discover(inputs, runner=runner)
    assert output_path.exists()


def test_run_discover_renders_prompt_with_inputs(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    inputs = _make_inputs(tmp_path, output_path=output_path)

    runner = _StubRunner(
        on_run=lambda prompt, env, path: _write_valid_yaml(path),
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    run_discover(inputs, runner=runner)

    assert runner.last_prompt is not None
    prompt = runner.last_prompt
    assert str(inputs.store_dir.resolve()) in prompt
    assert str(output_path) in prompt
    for target in inputs.targets:
        assert target.project.name in prompt
        assert any(item.rel_path in prompt for item in target.files)


def test_run_discover_propagates_environment(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    inputs = _make_inputs(tmp_path, output_path=output_path)

    runner = _StubRunner(
        on_run=lambda prompt, env, path: _write_valid_yaml(path),
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    run_discover(inputs, runner=runner)

    assert runner.last_env is not None
    env = runner.last_env
    assert env["CADENCE_MEMORY_DIR"] == str(inputs.store_dir.resolve())
    assert "PATH" in env
    assert env.get("PATH") == os.environ.get("PATH")
