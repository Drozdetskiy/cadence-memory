"""End-to-end tests for the `query` command's rerank wiring."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cadence_memory import cli as cli_module
from cadence_memory.cli import app
from cadence_memory.rerank.interface import RerankedItem, RerankItem

runner = CliRunner()


@dataclass
class _StubReranker:
    response: list[RerankedItem]
    model: str = "claude-haiku-4-5"
    calls: list[tuple[str, list[RerankItem]]] = field(default_factory=list)

    def rerank(
        self,
        query: str,
        items: Sequence[RerankItem],
    ) -> list[RerankedItem]:
        self.calls.append((query, list(items)))
        return list(self.response)


@dataclass
class _FactoryRecorder:
    response_builder: object  # Callable[[int], list[RerankedItem]]
    models: list[str] = field(default_factory=list)
    rerankers: list[_StubReranker] = field(default_factory=list)
    last_items_count: int = 0

    def __call__(self, model: str) -> _StubReranker:
        self.models.append(model)
        # Build deferred response based on the items the reranker will receive.
        # Tests can also pre-build a fixed response by ignoring items_count.
        reranker = _StubReranker(response=[], model=model)

        outer = self

        original_rerank = reranker.rerank

        def patched_rerank(
            query: str, items: Sequence[RerankItem]
        ) -> list[RerankedItem]:
            outer.last_items_count = len(items)
            reranker.response = list(outer.response_builder(list(items)))  # type: ignore[operator]
            return original_rerank(query, items)

        reranker.rerank = patched_rerank  # type: ignore[method-assign]
        outer.rerankers.append(reranker)
        return reranker


def _passthrough_response(items: list[RerankItem]) -> list[RerankedItem]:
    return [
        RerankedItem(chunk_id=item.chunk_id, score=10.0 - rank, rank=rank)
        for rank, item in enumerate(items)
    ]


def _reverse_response(items: list[RerankItem]) -> list[RerankedItem]:
    n = len(items)
    return [
        RerankedItem(chunk_id=item.chunk_id, score=float(rank), rank=n - 1 - rank)
        for rank, item in enumerate(items)
    ]


def _fallback_response(items: list[RerankItem]) -> list[RerankedItem]:
    # Mirrors ClaudeReranker._passthrough(): descending fallback scores.
    return [
        RerankedItem(chunk_id=item.chunk_id, score=5.0 - rank * 0.1, rank=rank)
        for rank, item in enumerate(items)
    ]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_store(
    tmp_path: Path,
    *,
    rerank_enabled: bool = True,
) -> Path:
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()

    _write(project_dir / "alpha.md", "# Alpha\n\nshared body content for query alpha\n")
    _write(project_dir / "beta.md", "# Beta\n\nshared sibling body for query beta\n")
    _write(project_dir / "gamma.md", "# Gamma\n\nshared trailing body for query gamma\n")

    rerank_block = (
        "query:\n  expansion:\n    enabled: false\n  rerank:\n    enabled: true\n"
        if rerank_enabled
        else "query:\n  expansion:\n    enabled: false\n  rerank:\n    enabled: false\n"
    )

    config_yaml = (
        f"projects:\n  - name: proj\n    path: {project_dir}\n"
        "defaults:\n  kind: doc\n"
        "enrichment:\n  enabled: false\n"
        f"{rerank_block}"
    )
    (store_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")
    (store_dir / "annotations-config.yaml").write_text(
        "documents:\n"
        "  - id: proj:alpha.md\n    project: proj\n    path: alpha.md\n    kind: doc\n"
        "  - id: proj:beta.md\n    project: proj\n    path: beta.md\n    kind: doc\n"
        "  - id: proj:gamma.md\n    project: proj\n    path: gamma.md\n    kind: doc\n",
        encoding="utf-8",
    )

    seed = runner.invoke(app, ["--store", str(store_dir), "reindex"])
    assert seed.exit_code == 0, seed.output
    return store_dir


def test_default_flow_invokes_reranker_and_reorders_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(response_builder=_reverse_response)
    monkeypatch.setattr(cli_module, "_reranker_factory", recorder)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "shared", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert len(recorder.rerankers) == 1
    assert recorder.rerankers[0].calls
    query_arg, _ = recorder.rerankers[0].calls[0]
    assert query_arg == "shared"
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) >= 2
    for entry in parsed:
        assert "score_rerank" in entry
    # Rerank scores should be non-increasing (sorted desc).
    rerank_scores = [entry["score_rerank"] for entry in parsed]
    assert rerank_scores == sorted(rerank_scores, reverse=True)
    assert "reranked top" in result.stderr


def test_no_rerank_skips_reranker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(response_builder=_passthrough_response)
    monkeypatch.setattr(cli_module, "_reranker_factory", recorder)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "shared",
            "--no-rerank",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert recorder.rerankers == []
    assert recorder.models == []
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert parsed
    for entry in parsed:
        assert "score_rerank" not in entry
    assert "reranked top" not in result.stderr


def test_rerank_top_k_limits_items_sent_to_reranker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(response_builder=_passthrough_response)
    monkeypatch.setattr(cli_module, "_reranker_factory", recorder)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "shared",
            "--rerank-top-k",
            "1",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(recorder.rerankers) == 1
    assert len(recorder.rerankers[0].calls) == 1
    _, items = recorder.rerankers[0].calls[0]
    assert len(items) == 1
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) >= 2
    assert "score_rerank" in parsed[0]
    for entry in parsed[1:]:
        assert "score_rerank" not in entry


def test_rerank_model_flag_threads_to_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(response_builder=_passthrough_response)
    monkeypatch.setattr(cli_module, "_reranker_factory", recorder)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "shared",
            "--rerank-model",
            "claude-sonnet-4-6",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert recorder.models == ["claude-sonnet-4-6"]


def test_config_disabled_skips_reranker_without_no_rerank(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path, rerank_enabled=False)

    recorder = _FactoryRecorder(response_builder=_passthrough_response)
    monkeypatch.setattr(cli_module, "_reranker_factory", recorder)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "shared", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert recorder.rerankers == []
    parsed = json.loads(result.stdout)
    for entry in parsed:
        assert "score_rerank" not in entry
    assert "reranked top" not in result.stderr


def test_fallback_passthrough_response_still_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = _make_store(tmp_path)

    recorder = _FactoryRecorder(response_builder=_fallback_response)
    monkeypatch.setattr(cli_module, "_reranker_factory", recorder)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "shared", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed
    rerank_scores = [entry["score_rerank"] for entry in parsed]
    assert rerank_scores == sorted(rerank_scores, reverse=True)
    # Fallback assigns descending values 5.0, 4.9, 4.8, ...
    assert rerank_scores[0] == pytest.approx(5.0)
    if len(rerank_scores) > 1:
        assert rerank_scores[1] == pytest.approx(4.9)


def test_body_excerpt_truncated_to_500_chars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()

    long_body = "shared " + ("x" * 1000)
    _write(project_dir / "long.md", f"# Long\n\n{long_body}\n")

    config_yaml = (
        f"projects:\n  - name: proj\n    path: {project_dir}\n"
        "defaults:\n  kind: doc\n"
        "enrichment:\n  enabled: false\n"
        "query:\n  expansion:\n    enabled: false\n  rerank:\n    enabled: true\n"
    )
    (store_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")
    (store_dir / "annotations-config.yaml").write_text(
        "documents:\n"
        "  - id: proj:long.md\n    project: proj\n    path: long.md\n    kind: doc\n",
        encoding="utf-8",
    )
    seed = runner.invoke(app, ["--store", str(store_dir), "reindex"])
    assert seed.exit_code == 0, seed.output

    recorder = _FactoryRecorder(response_builder=_passthrough_response)
    monkeypatch.setattr(cli_module, "_reranker_factory", recorder)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "shared", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert len(recorder.rerankers) == 1
    _, items = recorder.rerankers[0].calls[0]
    assert items
    assert len(items[0].body_excerpt) == 500


def test_rerank_top_k_negative_rejected(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "shared",
            "--rerank-top-k",
            "-1",
        ],
    )

    assert result.exit_code == 1
    assert "--rerank-top-k must be >= 0" in result.stderr


def test_query_help_documents_rerank_flags() -> None:
    result = runner.invoke(app, ["query", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--no-rerank", "--rerank-model", "--rerank-top-k"):
        assert flag in result.output


def test_default_reranker_factory_loads_packaged_prompt() -> None:
    from cadence_memory.cli import _default_reranker_factory
    from cadence_memory.rerank.claude_reranker import ClaudeReranker

    reranker = _default_reranker_factory("claude-haiku-4-5")

    assert isinstance(reranker, ClaudeReranker)
    assert reranker.model == "claude-haiku-4-5"
    template_text = reranker._template.template
    assert "$query" in template_text
    assert "$count" in template_text
    assert "$items_json" in template_text
