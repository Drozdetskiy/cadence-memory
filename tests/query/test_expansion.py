"""Unit tests for ClaudeQueryExpander."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cadence_memory.executor.claude_executor import RunResult
from cadence_memory.query.expansion import ClaudeQueryExpander, ExpansionResult
from cadence_memory.store.sqlite_store import SqliteStore


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


@dataclass
class _RaisingRunner:
    error: Exception = field(default_factory=lambda: RuntimeError("boom"))
    call_count: int = 0

    def run(
        self,
        prompt: str,
        *,
        env: Mapping[str, str] | None = None,
    ) -> RunResult:
        self.call_count += 1
        raise self.error


def _fixed_now() -> datetime:
    return datetime(2026, 5, 6, 12, 30, 0, tzinfo=UTC)


def _make_expander(
    response: str | RunResult,
    *,
    store: SqliteStore,
    model: str = "claude-haiku-4-5",
) -> tuple[ClaudeQueryExpander, _StubRunner]:
    if isinstance(response, RunResult):
        result = response
    else:
        result = RunResult(output=response, exit_code=0, idle_timed_out=False)

    runner = _StubRunner(on_run=lambda prompt, env: result)
    expander = ClaudeQueryExpander(
        runner=runner,
        model=model,
        store=store,
        now=_fixed_now,
    )
    return expander, runner


def test_happy_path_returns_variants_and_writes_cache(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    payload = {"variants": ["webhooks", "callbacks"]}
    expander, runner = _make_expander(json.dumps(payload, ensure_ascii=False), store=store)

    result = expander.expand("вебхуки")

    assert runner.call_count == 1
    assert result == ExpansionResult(
        original="вебхуки",
        variants=("вебхуки", "webhooks", "callbacks"),
        model="claude-haiku-4-5",
    )

    cached = store.expansion_cache_get("вебхуки", "claude-haiku-4-5")
    assert cached is not None
    assert json.loads(str(cached["variants_json"])) == ["webhooks", "callbacks"]
    assert cached["model"] == "claude-haiku-4-5"
    assert cached["generated_at"] == _fixed_now().isoformat()


def test_cache_hit_skips_runner(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    store.expansion_cache_put(
        query_text="вебхуки",
        model="claude-haiku-4-5",
        variants_json=json.dumps(["webhooks", "callbacks"]),
        generated_at="2026-01-01T00:00:00+00:00",
    )

    runner = _RaisingRunner()
    expander = ClaudeQueryExpander(
        runner=runner,
        model="claude-haiku-4-5",
        store=store,
        now=_fixed_now,
    )

    result = expander.expand("вебхуки")

    assert runner.call_count == 0
    assert result.variants == ("вебхуки", "webhooks", "callbacks")
    assert result.original == "вебхуки"
    assert result.model == "claude-haiku-4-5"


def test_fenced_json_block_is_parsed(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    payload = {"variants": ["alpha", "бета"]}
    fenced = f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    expander, _ = _make_expander(fenced, store=store)

    result = expander.expand("test query")

    assert result.variants == ("test query", "alpha", "бета")


def test_invalid_json_yields_fallback_and_warns_no_cache_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SqliteStore(tmp_path / "test.db")
    expander, runner = _make_expander("not json at all", store=store)

    result = expander.expand("query")

    assert runner.call_count == 1
    assert result == ExpansionResult(
        original="query",
        variants=("query",),
        model="claude-haiku-4-5",
    )
    captured = capsys.readouterr()
    assert captured.err.startswith("expansion:")
    assert "invalid JSON" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None


def test_non_zero_exit_yields_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SqliteStore(tmp_path / "test.db")
    expander, _ = _make_expander(
        RunResult(output="", exit_code=1, idle_timed_out=False),
        store=store,
    )

    result = expander.expand("query")

    assert result.variants == ("query",)
    captured = capsys.readouterr()
    assert "expansion:" in captured.err
    assert "status 1" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None


def test_idle_timeout_yields_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SqliteStore(tmp_path / "test.db")
    expander, _ = _make_expander(
        RunResult(output="", exit_code=137, idle_timed_out=True),
        store=store,
    )

    result = expander.expand("query")

    assert result.variants == ("query",)
    captured = capsys.readouterr()
    assert "idle timeout" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None


def test_empty_output_yields_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SqliteStore(tmp_path / "test.db")
    expander, _ = _make_expander("   \n  ", store=store)

    result = expander.expand("query")

    assert result.variants == ("query",)
    captured = capsys.readouterr()
    assert "empty output" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None


def test_runner_exception_yields_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SqliteStore(tmp_path / "test.db")
    runner = _RaisingRunner()
    expander = ClaudeQueryExpander(
        runner=runner,
        model="claude-haiku-4-5",
        store=store,
        now=_fixed_now,
    )

    result = expander.expand("query")

    assert result.variants == ("query",)
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "boom" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None


def test_max_variants_clamps_list_size(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    payload = {"variants": ["a", "b", "c", "d", "e"]}
    expander, _ = _make_expander(json.dumps(payload), store=store)

    result = expander.expand("query", max_variants=2)

    assert result.variants == ("query", "a", "b")
    cached = store.expansion_cache_get("query", "claude-haiku-4-5")
    assert cached is not None
    assert json.loads(str(cached["variants_json"])) == ["a", "b"]


def test_duplicates_empty_and_equal_to_original_are_filtered(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    payload = {
        "variants": [
            "  ",
            "alpha",
            "alpha",
            "query",
            "",
            "beta",
        ],
    }
    expander, _ = _make_expander(json.dumps(payload), store=store)

    result = expander.expand("query")

    assert result.variants == ("query", "alpha", "beta")
    cached = store.expansion_cache_get("query", "claude-haiku-4-5")
    assert cached is not None
    assert json.loads(str(cached["variants_json"])) == ["alpha", "beta"]


def test_prompt_contains_original_query_and_max_variants(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    payload = {"variants": ["a"]}
    expander, runner = _make_expander(json.dumps(payload), store=store)

    expander.expand("платежи Stripe", max_variants=4)

    assert runner.last_prompt is not None
    assert "платежи Stripe" in runner.last_prompt
    assert "4" in runner.last_prompt


def test_missing_variants_key_yields_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SqliteStore(tmp_path / "test.db")
    expander, _ = _make_expander(json.dumps({"other": ["x"]}), store=store)

    result = expander.expand("query")

    assert result.variants == ("query",)
    captured = capsys.readouterr()
    assert "expansion:" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None


def test_variants_not_a_list_yields_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SqliteStore(tmp_path / "test.db")
    expander, _ = _make_expander(json.dumps({"variants": "alpha"}), store=store)

    result = expander.expand("query")

    assert result.variants == ("query",)
    captured = capsys.readouterr()
    assert "expansion:" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None


def test_all_variants_filtered_yields_fallback_no_cache(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty post-filter variants must NOT poison the cache for future calls."""
    store = SqliteStore(tmp_path / "test.db")
    payload = {"variants": ["query", "  ", "query"]}
    expander, _ = _make_expander(json.dumps(payload), store=store)

    result = expander.expand("query")

    assert result.variants == ("query",)
    captured = capsys.readouterr()
    assert "expansion:" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None


def test_empty_variants_list_yields_fallback_no_cache(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SqliteStore(tmp_path / "test.db")
    expander, _ = _make_expander(json.dumps({"variants": []}), store=store)

    result = expander.expand("query")

    assert result.variants == ("query",)
    captured = capsys.readouterr()
    assert "expansion:" in captured.err
    assert store.expansion_cache_get("query", "claude-haiku-4-5") is None
