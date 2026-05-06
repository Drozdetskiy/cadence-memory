"""Unit tests for ClaudeReranker."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import pytest

from cadence_memory.executor.claude_executor import RunResult
from cadence_memory.rerank.claude_reranker import ClaudeReranker
from cadence_memory.rerank.interface import RerankItem


@dataclass
class _StubRunner:
    on_run: Callable[[str, Mapping[str, str]], RunResult]
    last_prompt: str | None = field(default=None)
    last_env: Mapping[str, str] | None = field(default=None)
    call_count: int = 0

    def run(
        self,
        prompt: str,
        *,
        env: Mapping[str, str] | None = None,
    ) -> RunResult:
        self.call_count += 1
        self.last_prompt = prompt
        self.last_env = env if env is not None else {}
        return self.on_run(prompt, self.last_env)


def _make_reranker(
    response: str | RunResult,
    *,
    model: str = "claude-haiku-4-5",
) -> tuple[ClaudeReranker, _StubRunner]:
    if isinstance(response, RunResult):
        result = response
    else:
        result = RunResult(output=response, exit_code=0, idle_timed_out=False)

    runner = _StubRunner(on_run=lambda prompt, env: result)
    reranker = ClaudeReranker(runner=runner, model=model)
    return reranker, runner


def _items(*chunk_ids: str) -> list[RerankItem]:
    return [
        RerankItem(
            chunk_id=cid,
            title=f"Title {cid}",
            heading_path=("Section",),
            summary=f"summary for {cid}",
            body_excerpt=f"body excerpt for {cid}",
        )
        for cid in chunk_ids
    ]


def test_happy_path_returns_sorted_reranked_items() -> None:
    payload = [
        {"chunk_id": "a", "score": 3.0},
        {"chunk_id": "b", "score": 9.5},
        {"chunk_id": "c", "score": 6.0},
    ]
    reranker, runner = _make_reranker(json.dumps(payload))

    result = reranker.rerank("any query", _items("a", "b", "c"))

    assert runner.call_count == 1
    assert [r.chunk_id for r in result] == ["b", "c", "a"]
    assert [r.rank for r in result] == [0, 1, 2]
    assert [r.score for r in result] == [9.5, 6.0, 3.0]
    assert runner.last_env is not None
    assert runner.last_env.get("ANTHROPIC_MODEL") == "claude-haiku-4-5"


def test_scores_out_of_range_are_clamped() -> None:
    payload = [
        {"chunk_id": "a", "score": -2.5},
        {"chunk_id": "b", "score": 99.0},
        {"chunk_id": "c", "score": 5.0},
    ]
    reranker, _ = _make_reranker(json.dumps(payload))

    result = reranker.rerank("q", _items("a", "b", "c"))

    by_id = {r.chunk_id: r.score for r in result}
    assert by_id["a"] == 0.0
    assert by_id["b"] == 10.0
    assert by_id["c"] == 5.0


def test_fenced_json_block_is_parsed() -> None:
    payload = [{"chunk_id": "a", "score": 7.0}, {"chunk_id": "b", "score": 8.0}]
    fenced = f"```json\n{json.dumps(payload)}\n```"
    reranker, _ = _make_reranker(fenced)

    result = reranker.rerank("q", _items("a", "b"))

    assert [r.chunk_id for r in result] == ["b", "a"]


def test_invalid_json_triggers_passthrough_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reranker, _ = _make_reranker("not json")

    result = reranker.rerank("q", _items("a", "b", "c"))

    assert [r.chunk_id for r in result] == ["a", "b", "c"]
    assert [r.rank for r in result] == [0, 1, 2]
    assert [r.score for r in result] == [5.0, 4.9, 4.8]
    captured = capsys.readouterr()
    assert captured.err.startswith("rerank:")
    assert "invalid JSON" in captured.err


def test_runner_exception_triggers_passthrough_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @dataclass
    class _RaisingRunner:
        def run(
            self,
            prompt: str,
            *,
            env: Mapping[str, str] | None = None,
        ) -> RunResult:
            raise RuntimeError("boom")

    reranker = ClaudeReranker(runner=_RaisingRunner(), model="m")

    result = reranker.rerank("q", _items("a", "b"))

    assert [r.chunk_id for r in result] == ["a", "b"]
    assert [r.score for r in result] == [5.0, 4.9]
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "boom" in captured.err


def test_non_zero_exit_triggers_passthrough_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reranker, _ = _make_reranker(
        RunResult(output="", exit_code=1, idle_timed_out=False),
    )

    result = reranker.rerank("q", _items("a", "b"))

    assert [r.chunk_id for r in result] == ["a", "b"]
    captured = capsys.readouterr()
    assert "status 1" in captured.err


def test_idle_timeout_triggers_passthrough_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reranker, _ = _make_reranker(
        RunResult(output="", exit_code=137, idle_timed_out=True),
    )

    result = reranker.rerank("q", _items("a", "b"))

    assert [r.chunk_id for r in result] == ["a", "b"]
    captured = capsys.readouterr()
    assert "idle timeout" in captured.err


def test_empty_output_triggers_passthrough_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reranker, _ = _make_reranker("   \n  ")

    result = reranker.rerank("q", _items("a", "b"))

    assert [r.chunk_id for r in result] == ["a", "b"]
    captured = capsys.readouterr()
    assert "empty output" in captured.err


def test_top_level_not_a_list_triggers_passthrough_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reranker, _ = _make_reranker(json.dumps({"chunk_id": "a", "score": 5.0}))

    result = reranker.rerank("q", _items("a", "b"))

    assert [r.chunk_id for r in result] == ["a", "b"]
    captured = capsys.readouterr()
    assert "must be a list" in captured.err


def test_missing_chunk_ids_get_score_zero_and_warn(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = [{"chunk_id": "a", "score": 7.0}]
    reranker, _ = _make_reranker(json.dumps(payload))

    result = reranker.rerank("q", _items("a", "b", "c"))

    by_id = {r.chunk_id: r.score for r in result}
    assert by_id["a"] == 7.0
    assert by_id["b"] == 0.0
    assert by_id["c"] == 0.0
    assert [r.chunk_id for r in result] == ["a", "b", "c"]
    captured = capsys.readouterr()
    assert "rerank:" in captured.err
    assert "missing" in captured.err


def test_ties_break_by_input_order() -> None:
    payload = [
        {"chunk_id": "a", "score": 5.0},
        {"chunk_id": "b", "score": 5.0},
        {"chunk_id": "c", "score": 5.0},
    ]
    reranker, _ = _make_reranker(json.dumps(payload))

    result = reranker.rerank("q", _items("c", "a", "b"))

    assert [r.chunk_id for r in result] == ["c", "a", "b"]


def test_prompt_includes_query_count_and_summaries() -> None:
    payload = [{"chunk_id": "a", "score": 5.0}, {"chunk_id": "b", "score": 5.0}]
    reranker, runner = _make_reranker(json.dumps(payload))

    items = [
        RerankItem(
            chunk_id="a",
            title="Alpha doc",
            heading_path=("Top",),
            summary="alpha summary",
            body_excerpt="alpha body excerpt",
        ),
        RerankItem(
            chunk_id="b",
            title="Beta doc",
            heading_path=("Top", "Sub"),
            summary="beta summary",
            body_excerpt="beta body excerpt",
        ),
    ]
    reranker.rerank("how do webhooks work?", items)

    assert runner.last_prompt is not None
    prompt = runner.last_prompt
    assert "how do webhooks work?" in prompt
    assert "alpha summary" in prompt
    assert "beta summary" in prompt
    assert "Alpha doc" in prompt
    assert "Beta doc" in prompt
    # Count placeholder is rendered.
    assert "2 candidate chunks" in prompt


def test_body_excerpt_used_when_summary_is_none() -> None:
    payload = [{"chunk_id": "a", "score": 5.0}]
    reranker, runner = _make_reranker(json.dumps(payload))

    item = RerankItem(
        chunk_id="a",
        title="t",
        heading_path=(),
        summary=None,
        body_excerpt="THE BODY EXCERPT",
    )
    reranker.rerank("q", [item])

    assert runner.last_prompt is not None
    assert "THE BODY EXCERPT" in runner.last_prompt


def test_empty_items_returns_empty_list_without_runner_call() -> None:
    reranker, runner = _make_reranker("[]")

    result = reranker.rerank("q", [])

    assert result == []
    assert runner.call_count == 0
