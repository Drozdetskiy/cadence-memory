"""run_discover() with DiscoverInputs/DiscoverTarget; renders prompt and validates Claude output."""

from __future__ import annotations

import importlib.resources
import os
from dataclasses import dataclass
from pathlib import Path
from string import Template

import yaml

from cadence_memory.config import ConfigError, ProjectConfig, load_annotations_config
from cadence_memory.discover.scanner import DiscoveredFile
from cadence_memory.executor.claude_executor import ClaudeRunner

__all__ = [
    "DiscoverFailed",
    "DiscoverInputs",
    "DiscoverInvalidOutput",
    "DiscoverTarget",
    "DiscoverTimedOut",
    "run_discover",
]


class DiscoverFailed(RuntimeError):
    """Claude exited with a non-zero status."""

    def __init__(self, exit_code: int) -> None:
        super().__init__(f"claude exited with status {exit_code}")
        self.exit_code = exit_code


class DiscoverTimedOut(RuntimeError):
    """Claude was killed by the idle watchdog."""

    def __init__(self) -> None:
        super().__init__("claude exceeded the idle timeout")


class DiscoverInvalidOutput(RuntimeError):
    """Claude produced no output file or an invalid annotations-config."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"invalid discover output at {path}: {message}")
        self.path = path
        self.message = message


@dataclass(frozen=True, slots=True)
class DiscoverTarget:
    project: ProjectConfig
    files: tuple[DiscoveredFile, ...]


@dataclass(frozen=True, slots=True)
class DiscoverInputs:
    store_dir: Path
    targets: tuple[DiscoverTarget, ...]
    output_path: Path


def _render_projects_block(targets: tuple[DiscoverTarget, ...]) -> str:
    entries: list[dict[str, object]] = []
    for target in targets:
        kind_rules = [
            {"pattern": rule.pattern, "kind": rule.kind}
            for rule in target.project.discover.kind_rules
        ]
        files = [item.rel_path for item in target.files]
        entries.append(
            {
                "name": target.project.name,
                "path": str(target.project.path),
                "kind_rules": kind_rules,
                "files": files,
            }
        )
    return yaml.safe_dump(entries, sort_keys=False)


def _render_prompt(inputs: DiscoverInputs) -> str:
    template_text = (
        importlib.resources.files("cadence_memory.defaults.prompts")
        .joinpath("discover.txt")
        .read_text(encoding="utf-8")
    )
    template = Template(template_text)
    return template.substitute(
        store_dir=str(inputs.store_dir.resolve()),
        output_path=str(inputs.output_path),
        projects_block=_render_projects_block(inputs.targets),
    )


def run_discover(inputs: DiscoverInputs, *, runner: ClaudeRunner) -> None:
    """Render the discover prompt, invoke Claude, and validate the output YAML."""
    prompt = _render_prompt(inputs)
    env = dict(os.environ)
    env["CADENCE_MEMORY_DIR"] = str(inputs.store_dir.resolve())

    result = runner.run(prompt, env=env)

    if result.idle_timed_out:
        raise DiscoverTimedOut()
    if result.exit_code != 0:
        raise DiscoverFailed(result.exit_code)
    if not inputs.output_path.exists():
        raise DiscoverInvalidOutput(
            inputs.output_path,
            "claude did not produce the output file",
        )

    try:
        load_annotations_config(inputs.output_path, config=None)
    except ConfigError as exc:
        raise DiscoverInvalidOutput(inputs.output_path, str(exc)) from exc
