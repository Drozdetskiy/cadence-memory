"""End-to-end tests for the `query` command's expansion wiring."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cadence_memory import cli as cli_module
from cadence_memory.cli import app
from cadence_memory.query.expansion import ExpansionResult
from cadence_memory.store.interface import Store

runner = CliRunner()


@dataclass
class _StubExpander:
    response: ExpansionResult
    calls: list[tuple[str, int]] = field(default_factory=list)

    def expand(self, query: str, *, max_variants: int = 3) -> ExpansionResult:
        self.calls.append((query, max_variants))
        return self.response


@dataclass
class _FactoryRecorder:
    response: ExpansionResult
    models: list[str] = field(default_factory=list)
    expanders: list[_StubExpander] = field(default_factory=list)

    def __call__(self, model: str, store: Store) -> _StubExpander:
        self.models.append(model)
        expander = _StubExpander(response=self.response)
        self.expanders.append(expander)
        return expander


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_store(
    tmp_path: Path,
    *,
    expansion_enabled: bool = True,
) -> Path:
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()

    _write(project_dir / "alpha.md", "# Alpha\n\nuniqalpha body content here\n")
    _write(project_dir / "beta.md", "# Beta\n\nbeta sibling token uniqalpha\n")

    expansion_block = (
        "query:\n  expansion:\n    enabled: true\n"
        if expansion_enabled
        else "query:\n  expansion:\n    enabled: false\n"
    )

    config_yaml = (
        f"projects:\n  - name: proj\n    path: {project_dir}\n"
        "defaults:\n  kind: doc\n"
        "enrichment:\n  enabled: false\n"
        f"{expansion_block}"
    )
    (store_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")
    (store_dir / "annotations-config.yaml").write_text(
        "documents:\n"
        "  - id: proj:alpha.md\n    project: proj\n    path: alpha.md\n    kind: doc\n"
        "  - id: proj:beta.md\n    project: proj\n    path: beta.md\n    kind: doc\n",
        encoding="utf-8",
    )

    seed = runner.invoke(app, ["--store", str(store_dir), "reindex"])
    assert seed.exit_code == 0, seed.output
    return store_dir


@dataclass
class _StoreQuerySpy:
    last_queries: list[tuple[str, ...]] = field(default_factory=list)


def _install_store_spy(monkeypatch: pytest.MonkeyPatch) -> _StoreQuerySpy:
    spy = _StoreQuerySpy()
    real_query = cli_module.SqliteStore.query

    def patched_query(
        self: object,
        queries: Sequence[str],
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
        boost: bool = True,
    ) -> object:
        spy.last_queries.append(tuple(queries))
        return real_query(  # type: ignore[misc]
            self, queries, kind=kind, project=project, limit=limit, boost=boost
        )

    monkeypatch.setattr(cli_module.SqliteStore, "query", patched_query)
    return spy


def test_no_expand_skips_expander(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(
        response=ExpansionResult(
            original="uniqalpha",
            variants=("uniqalpha", "extra one", "extra two"),
            model="claude-haiku-4-5",
        )
    )
    monkeypatch.setattr(cli_module, "_expander_factory", recorder)

    store_spy = _install_store_spy(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "uniqalpha",
            "--no-expand",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert recorder.expanders == []
    assert recorder.models == []
    assert store_spy.last_queries[-1] == ("uniqalpha",)
    assert "expanded into" not in result.output


def test_default_flow_invokes_expander_and_uses_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(
        response=ExpansionResult(
            original="uniqalpha",
            variants=("uniqalpha", "alpha synonym", "alpha synonym two"),
            model="claude-haiku-4-5",
        )
    )
    monkeypatch.setattr(cli_module, "_expander_factory", recorder)
    store_spy = _install_store_spy(monkeypatch)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "uniqalpha", "--format", "json"],
    )

    assert result.exit_code == 0, result.stdout
    assert len(recorder.expanders) == 1
    assert recorder.expanders[0].calls == [("uniqalpha", 3)]
    assert store_spy.last_queries[-1] == (
        "uniqalpha",
        "alpha synonym",
        "alpha synonym two",
    )
    assert "expanded into 3 variants:" in result.stderr
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)


def test_variants_flag_overrides_max_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(
        response=ExpansionResult(
            original="uniqalpha",
            variants=("uniqalpha", "v1"),
            model="claude-haiku-4-5",
        )
    )
    monkeypatch.setattr(cli_module, "_expander_factory", recorder)
    _install_store_spy(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "uniqalpha",
            "--variants",
            "5",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert recorder.expanders[0].calls == [("uniqalpha", 5)]


def test_expansion_model_flag_threads_to_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(
        response=ExpansionResult(
            original="uniqalpha",
            variants=("uniqalpha",),
            model="claude-sonnet-4-6",
        )
    )
    monkeypatch.setattr(cli_module, "_expander_factory", recorder)
    _install_store_spy(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "uniqalpha",
            "--expansion-model",
            "claude-sonnet-4-6",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert recorder.models == ["claude-sonnet-4-6"]


def test_config_disabled_skips_expander_without_no_expand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path, expansion_enabled=False)

    recorder = _FactoryRecorder(
        response=ExpansionResult(
            original="uniqalpha",
            variants=("uniqalpha", "x"),
            model="claude-haiku-4-5",
        )
    )
    monkeypatch.setattr(cli_module, "_expander_factory", recorder)
    store_spy = _install_store_spy(monkeypatch)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "uniqalpha", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert recorder.expanders == []
    assert store_spy.last_queries[-1] == ("uniqalpha",)
    assert "expanded into" not in result.output


def test_single_variant_fallback_emits_no_stderr_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(
        response=ExpansionResult(
            original="uniqalpha",
            variants=("uniqalpha",),
            model="claude-haiku-4-5",
        )
    )
    monkeypatch.setattr(cli_module, "_expander_factory", recorder)
    store_spy = _install_store_spy(monkeypatch)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "uniqalpha", "--format", "json"],
    )

    assert result.exit_code == 0, result.stdout
    assert recorder.expanders[0].calls == [("uniqalpha", 3)]
    assert store_spy.last_queries[-1] == ("uniqalpha",)
    assert "expanded into" not in result.stderr


def test_query_help_documents_expansion_flags() -> None:
    result = runner.invoke(app, ["query", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--no-expand", "--expansion-model", "--variants"):
        assert flag in result.output


def test_default_expander_factory_loads_packaged_prompt(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)

    from cadence_memory.cli import _default_expander_factory
    from cadence_memory.store.sqlite_store import SqliteStore

    store = SqliteStore(store_dir / "index.sqlite")
    try:
        expander = _default_expander_factory("claude-haiku-4-5", store)
    finally:
        store.close()

    from cadence_memory.query.expansion import ClaudeQueryExpander

    assert isinstance(expander, ClaudeQueryExpander)
    assert expander.model == "claude-haiku-4-5"
    template_text = expander._template.template
    assert "$query" in template_text
    assert "$max_variants" in template_text
