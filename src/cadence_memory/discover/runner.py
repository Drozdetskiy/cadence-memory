"""run_discover() with DiscoverInputs/DiscoverTarget; renders prompt and validates Claude output."""

from __future__ import annotations

import importlib.resources
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template

import yaml

from cadence_memory.config import ConfigError, ProjectConfig, load_annotations_config
from cadence_memory.discover.scanner import DiscoveredFile
from cadence_memory.documents.hashes import content_hash
from cadence_memory.executor.claude_executor import ClaudeRunner
from cadence_memory.store.interface import Store

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
    store: Store | None = None
    cache_read: bool = True


def _cache_path_for(file: DiscoveredFile) -> str:
    return f"{file.project or ''}:{file.rel_path}"


def _cache_path_for_record(record: dict[str, object]) -> str | None:
    path = record.get("path")
    if not isinstance(path, str):
        return None
    project = record.get("project")
    if project is None:
        return f":{path}"
    if not isinstance(project, str):
        return None
    return f"{project}:{path}"


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


def _render_prompt(
    targets: tuple[DiscoverTarget, ...],
    store_dir: Path,
    output_path: Path,
) -> str:
    template_text = (
        importlib.resources.files("cadence_memory.defaults.prompts")
        .joinpath("discover.txt")
        .read_text(encoding="utf-8")
    )
    template = Template(template_text)
    return template.substitute(
        store_dir=str(store_dir.resolve()),
        output_path=str(output_path),
        projects_block=_render_projects_block(targets),
    )


def _compute_file_hashes(targets: tuple[DiscoverTarget, ...]) -> dict[str, str]:
    file_hashes: dict[str, str] = {}
    for target in targets:
        for file in target.files:
            try:
                text = file.abs_path.read_text(encoding="utf-8")
            except OSError, UnicodeDecodeError:
                continue
            file_hashes[_cache_path_for(file)] = content_hash(text)
    return file_hashes


def _filter_targets(
    targets: tuple[DiscoverTarget, ...], cached_paths: set[str]
) -> tuple[DiscoverTarget, ...]:
    filtered: list[DiscoverTarget] = []
    for target in targets:
        remaining = tuple(f for f in target.files if _cache_path_for(f) not in cached_paths)
        if remaining:
            filtered.append(DiscoverTarget(project=target.project, files=remaining))
    return tuple(filtered)


def _sort_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    def key(record: dict[str, object]) -> tuple[str, str]:
        project = record.get("project")
        path = record.get("path")
        return (
            project if isinstance(project, str) else "",
            path if isinstance(path, str) else "",
        )

    return sorted(records, key=key)


def _read_new_records(output_path: Path) -> list[dict[str, object]]:
    parsed = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        return []
    docs = parsed.get("documents")
    if not isinstance(docs, list):
        return []
    return [d for d in docs if isinstance(d, dict)]


def _validate_output(output_path: Path) -> None:
    try:
        load_annotations_config(output_path, config=None)
    except ConfigError as exc:
        raise DiscoverInvalidOutput(output_path, str(exc)) from exc


def _check_merged_unique_ids(records: list[dict[str, object]], output_path: Path) -> None:
    seen: set[str] = set()
    for index, record in enumerate(records):
        id_val = record.get("id")
        if not isinstance(id_val, str):
            continue
        if id_val in seen:
            raise DiscoverInvalidOutput(
                output_path,
                f"documents[{index}].id is a duplicate of an earlier entry ({id_val!r})",
            )
        seen.add(id_val)


def run_discover(inputs: DiscoverInputs, *, runner: ClaudeRunner) -> None:
    """Render the discover prompt, invoke Claude, and validate the output YAML."""
    file_hashes = _compute_file_hashes(inputs.targets) if inputs.store is not None else {}

    cached_records: list[dict[str, object]] = []
    cached_paths: set[str] = set()
    if inputs.store is not None and inputs.cache_read:
        for cache_path, hash_ in file_hashes.items():
            hit = inputs.store.discover_cache_get(path=cache_path, content_hash=hash_)
            if hit is not None:
                cached_records.append(hit)
                cached_paths.add(cache_path)

    targets_for_prompt = (
        _filter_targets(inputs.targets, cached_paths) if cached_paths else inputs.targets
    )

    if not targets_for_prompt:
        merged = _sort_records(cached_records)
        _check_merged_unique_ids(merged, inputs.output_path)
        inputs.output_path.write_text(
            yaml.safe_dump({"documents": merged}, sort_keys=False),
            encoding="utf-8",
        )
        print(
            f"discover: all {len(cached_records)} file(s) cached, skipping LLM",
            file=sys.stderr,
        )
        return

    prompt = _render_prompt(targets_for_prompt, inputs.store_dir, inputs.output_path)
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

    _validate_output(inputs.output_path)

    if inputs.store is None:
        return

    new_records = _read_new_records(inputs.output_path)

    if cached_records:
        merged = _sort_records(cached_records + new_records)
        _check_merged_unique_ids(merged, inputs.output_path)
        inputs.output_path.write_text(
            yaml.safe_dump({"documents": merged}, sort_keys=False),
            encoding="utf-8",
        )

    for record in new_records:
        record_cache_path = _cache_path_for_record(record)
        if record_cache_path is None:
            continue
        record_hash = file_hashes.get(record_cache_path)
        if record_hash is None:
            continue
        inputs.store.discover_cache_put(
            path=record_cache_path,
            content_hash=record_hash,
            annotation_json=json.dumps(record, sort_keys=True, ensure_ascii=False),
            model="claude-cli",
        )
