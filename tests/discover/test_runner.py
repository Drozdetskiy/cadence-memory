"""Unit tests for the discover orchestrator."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

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
    _cache_path_for,
    run_discover,
)
from cadence_memory.discover.scanner import DiscoveredFile
from cadence_memory.documents.hashes import content_hash
from cadence_memory.executor.claude_executor import RunResult
from cadence_memory.store.interface import StoredDocument


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


class _FakeStore:
    def __init__(self) -> None:
        self.cache: dict[tuple[str, str], dict[str, object]] = {}
        self.put_calls: list[tuple[str, str, str, str]] = []

    def upsert(self, doc: StoredDocument) -> None:
        raise NotImplementedError

    def delete(self, doc_id: str) -> None:
        raise NotImplementedError

    def get(self, doc_id: str) -> StoredDocument | None:
        raise NotImplementedError

    def list(
        self,
        *,
        kind: str | None = None,
        project: str | None = None,
        source_type: str | None = None,
    ) -> list[StoredDocument]:
        raise NotImplementedError

    def query(
        self,
        text: str,
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> list[StoredDocument]:
        raise NotImplementedError

    def all_ids(self) -> set[str]:
        raise NotImplementedError

    def discover_cache_get(self, *, path: str, content_hash: str) -> dict[str, object] | None:
        return self.cache.get((path, content_hash))

    def discover_cache_put(
        self,
        *,
        path: str,
        content_hash: str,
        annotation_json: str,
        model: str,
    ) -> None:
        self.put_calls.append((path, content_hash, annotation_json, model))
        self.cache[(path, content_hash)] = json.loads(annotation_json)

    def discover_cache_clear(self) -> None:
        self.cache.clear()

    def close(self) -> None:
        return None


def _make_cache_inputs(
    tmp_path: Path,
    *,
    output_path: Path,
    store: _FakeStore,
    cache_read: bool = True,
) -> tuple[DiscoverInputs, dict[str, str]]:
    """Create inputs with real on-disk markdown files; return inputs + cache_path->hash map."""
    targets: list[DiscoverTarget] = []
    file_specs = (
        ("alpha", ("README.md", "docs/guide.md")),
        ("beta", ("notes.md",)),
    )
    file_hashes: dict[str, str] = {}
    for name, rel_paths in file_specs:
        project_root = tmp_path / "projects" / name
        project_root.mkdir(parents=True, exist_ok=True)
        project = ProjectConfig(
            name=name,
            path=project_root,
            exclude=(),
            discover=DiscoverConfig(kind_rules=()),
        )
        files = []
        for rel in rel_paths:
            abs_path = project_root / rel
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            text = f"# {name}/{rel}\nbody for {rel}\n"
            abs_path.write_text(text, encoding="utf-8")
            file_hashes[f"{name}:{rel}"] = content_hash(text)
            files.append(
                DiscoveredFile(
                    project=name,
                    abs_path=abs_path,
                    rel_path=rel,
                )
            )
        targets.append(DiscoverTarget(project=project, files=tuple(files)))
    store_dir = tmp_path / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    return (
        DiscoverInputs(
            store_dir=store_dir,
            targets=tuple(targets),
            output_path=output_path,
            store=store,
            cache_read=cache_read,
        ),
        file_hashes,
    )


def _yaml_for(records: list[dict[str, object]]) -> str:
    return yaml.safe_dump({"documents": records}, sort_keys=False)


def test_run_discover_all_cached_skips_runner(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    store = _FakeStore()
    inputs, file_hashes = _make_cache_inputs(tmp_path, output_path=output_path, store=store)

    expected_records: list[dict[str, object]] = []
    for cache_path, hash_ in file_hashes.items():
        project, _, rel = cache_path.partition(":")
        record: dict[str, object] = {
            "id": cache_path,
            "path": rel,
            "project": project,
            "kind": "doc",
            "title": rel,
            "tags": ["readme"],
        }
        store.cache[(cache_path, hash_)] = record
        expected_records.append(record)

    invoked = {"called": False}

    def writer(prompt: str, env: Mapping[str, str], path: Path) -> None:
        invoked["called"] = True
        _write_valid_yaml(path)

    runner = _StubRunner(
        on_run=writer,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    run_discover(inputs, runner=runner)

    assert invoked["called"] is False
    parsed = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    docs = parsed["documents"]
    assert len(docs) == len(expected_records)
    ids = {entry["id"] for entry in docs}
    assert ids == {entry["id"] for entry in expected_records}


def test_run_discover_partial_cache_filters_prompt(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    store = _FakeStore()
    inputs, file_hashes = _make_cache_inputs(tmp_path, output_path=output_path, store=store)

    cached_path = "alpha:README.md"
    cached_record: dict[str, object] = {
        "id": cached_path,
        "path": "README.md",
        "project": "alpha",
        "kind": "doc",
        "title": "Cached Readme",
    }
    store.cache[(cached_path, file_hashes[cached_path])] = cached_record

    def writer(prompt: str, env: Mapping[str, str], path: Path) -> None:
        path.write_text(
            _yaml_for(
                [
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
            ),
            encoding="utf-8",
        )

    runner = _StubRunner(
        on_run=writer,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    run_discover(inputs, runner=runner)

    assert runner.last_prompt is not None
    prompt = runner.last_prompt
    assert "README.md" not in prompt
    assert "docs/guide.md" in prompt
    assert "notes.md" in prompt

    parsed = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    docs = parsed["documents"]
    ids = [entry["id"] for entry in docs]
    assert ids == sorted(ids)
    assert {entry["id"] for entry in docs} == {
        "alpha:README.md",
        "alpha:docs/guide.md",
        "beta:notes.md",
    }


def test_run_discover_writes_to_cache_after_success(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    store = _FakeStore()
    inputs, file_hashes = _make_cache_inputs(tmp_path, output_path=output_path, store=store)

    new_records = [
        {
            "id": "alpha:README.md",
            "path": "README.md",
            "project": "alpha",
            "kind": "doc",
            "title": "R",
        },
        {
            "id": "alpha:docs/guide.md",
            "path": "docs/guide.md",
            "project": "alpha",
            "kind": "doc",
            "title": "G",
        },
        {
            "id": "beta:notes.md",
            "path": "notes.md",
            "project": "beta",
            "kind": "doc",
            "title": "N",
        },
    ]

    def writer(prompt: str, env: Mapping[str, str], path: Path) -> None:
        path.write_text(_yaml_for(new_records), encoding="utf-8")

    runner = _StubRunner(
        on_run=writer,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    run_discover(inputs, runner=runner)

    assert len(store.put_calls) == len(new_records)
    written_paths = {call[0] for call in store.put_calls}
    assert written_paths == set(file_hashes.keys())
    for path, hash_, _annotation_json, model in store.put_calls:
        assert hash_ == file_hashes[path]
        assert model == "claude-cli"


def test_run_discover_skips_non_utf8_files_without_crashing(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    store = _FakeStore()
    inputs, file_hashes = _make_cache_inputs(tmp_path, output_path=output_path, store=store)

    bad_file = inputs.targets[0].files[0].abs_path
    bad_file.write_bytes(b"# title\n\xff\xfe not valid utf-8\n")
    file_hashes.pop(_cache_path_for(inputs.targets[0].files[0]))

    written = [
        {
            "id": "alpha:docs/guide.md",
            "path": "docs/guide.md",
            "project": "alpha",
            "kind": "doc",
            "title": "G",
        },
        {
            "id": "beta:notes.md",
            "path": "notes.md",
            "project": "beta",
            "kind": "doc",
            "title": "N",
        },
    ]

    def writer(prompt: str, env: Mapping[str, str], path: Path) -> None:
        path.write_text(_yaml_for(written), encoding="utf-8")

    runner = _StubRunner(
        on_run=writer,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    run_discover(inputs, runner=runner)

    cached_paths = {call[0] for call in store.put_calls}
    assert "alpha:README.md" not in cached_paths
    assert cached_paths == set(file_hashes.keys())


def test_run_discover_revalidates_merged_output(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    store = _FakeStore()
    inputs, file_hashes = _make_cache_inputs(tmp_path, output_path=output_path, store=store)

    cached_path = "alpha:README.md"
    cached_record: dict[str, object] = {
        "id": cached_path,
        "path": "README.md",
        "project": "alpha",
        "kind": "doc",
        "title": "Cached",
    }
    store.cache[(cached_path, file_hashes[cached_path])] = cached_record

    def writer(prompt: str, env: Mapping[str, str], path: Path) -> None:
        path.write_text(
            _yaml_for(
                [
                    {
                        "id": "alpha:README.md",
                        "path": "README.md",
                        "project": "alpha",
                        "kind": "doc",
                        "title": "Hallucinated",
                    },
                    {
                        "id": "alpha:docs/guide.md",
                        "path": "docs/guide.md",
                        "project": "alpha",
                        "kind": "doc",
                        "title": "G",
                    },
                    {
                        "id": "beta:notes.md",
                        "path": "notes.md",
                        "project": "beta",
                        "kind": "doc",
                        "title": "N",
                    },
                ]
            ),
            encoding="utf-8",
        )

    runner = _StubRunner(
        on_run=writer,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    with pytest.raises(DiscoverInvalidOutput):
        run_discover(inputs, runner=runner)

    assert store.cache[(cached_path, file_hashes[cached_path])] == cached_record
    assert store.put_calls == []
    on_disk = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    on_disk_ids = [d["id"] for d in on_disk["documents"]]
    assert on_disk_ids == [
        "alpha:README.md",
        "alpha:docs/guide.md",
        "beta:notes.md",
    ]


def test_run_discover_cache_read_false_bypasses_get_but_writes(tmp_path: Path) -> None:
    output_path = tmp_path / "annotations.proposed.yaml"
    store = _FakeStore()
    inputs, file_hashes = _make_cache_inputs(
        tmp_path, output_path=output_path, store=store, cache_read=False
    )

    cached_path = "alpha:README.md"
    store.cache[(cached_path, file_hashes[cached_path])] = {
        "id": cached_path,
        "path": "README.md",
        "project": "alpha",
        "kind": "doc",
        "title": "Stale",
    }

    new_records = [
        {
            "id": "alpha:README.md",
            "path": "README.md",
            "project": "alpha",
            "kind": "doc",
            "title": "Fresh",
        },
        {
            "id": "alpha:docs/guide.md",
            "path": "docs/guide.md",
            "project": "alpha",
            "kind": "doc",
            "title": "G",
        },
        {
            "id": "beta:notes.md",
            "path": "notes.md",
            "project": "beta",
            "kind": "doc",
            "title": "N",
        },
    ]

    invoked: list[str] = []

    def writer(prompt: str, env: Mapping[str, str], path: Path) -> None:
        invoked.append(prompt)
        path.write_text(_yaml_for(new_records), encoding="utf-8")

    runner = _StubRunner(
        on_run=writer,
        result=RunResult(output="", exit_code=0, idle_timed_out=False),
        output_path=output_path,
    )

    run_discover(inputs, runner=runner)

    assert len(invoked) == 1
    assert "README.md" in invoked[0]
    assert len(store.put_calls) == len(new_records)
    assert store.cache[(cached_path, file_hashes[cached_path])]["title"] == "Fresh"
