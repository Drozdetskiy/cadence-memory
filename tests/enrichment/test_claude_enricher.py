"""Unit tests for ClaudeEnricher."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from cadence_memory.enrichment.claude_enricher import MAX_KEYWORDS, ClaudeEnricher
from cadence_memory.enrichment.interface import EnrichmentResult
from cadence_memory.executor.claude_executor import RunResult


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


def _fixed_now() -> datetime:
    return datetime(2026, 5, 6, 12, 30, 0, tzinfo=UTC)


def _make_enricher(
    response: str | RunResult,
    *,
    model: str = "claude-haiku-4-5",
) -> tuple[ClaudeEnricher, _StubRunner]:
    if isinstance(response, RunResult):
        result = response
    else:
        result = RunResult(output=response, exit_code=0, idle_timed_out=False)

    runner = _StubRunner(on_run=lambda prompt, env: result)
    enricher = ClaudeEnricher(runner=runner, model=model, now=_fixed_now)
    return enricher, runner


def test_happy_path_returns_populated_result() -> None:
    payload = {
        "keywords": ["webhook", "вебхук", "Stripe events"],
        "questions": ["как обработать вебхук?", "where do we handle webhooks?"],
        "alt_phrasings": ["asynchronous event handling"],
    }
    enricher, runner = _make_enricher(json.dumps(payload, ensure_ascii=False))

    result = enricher.enrich_chunk(
        title="Stripe events",
        heading_path=("Webhooks", "Stripe"),
        body="We handle Stripe webhook events here.",
        summary="webhook handler overview",
    )

    assert runner.call_count == 1
    assert result.keywords == ("webhook", "вебхук", "Stripe events")
    assert result.questions == (
        "как обработать вебхук?",
        "where do we handle webhooks?",
    )
    assert result.alt_phrasings == ("asynchronous event handling",)
    assert result.model == "claude-haiku-4-5"
    assert result.generated_at == _fixed_now().isoformat()

    index_text = result.to_index_text()
    for term in (
        "webhook",
        "вебхук",
        "Stripe events",
        "как обработать вебхук?",
        "asynchronous event handling",
    ):
        assert term in index_text


def test_fenced_json_block_is_parsed() -> None:
    payload = {
        "keywords": ["alpha", "бета"],
        "questions": ["q1?"],
        "alt_phrasings": ["other angle"],
    }
    fenced = f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    enricher, _ = _make_enricher(fenced)

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="body-text", summary=None
    )

    assert result.keywords == ("alpha", "бета")
    assert result.questions == ("q1?",)
    assert result.alt_phrasings == ("other angle",)


def test_invalid_json_yields_empty_result_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    enricher, runner = _make_enricher("not json at all")

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert runner.call_count == 1
    assert result == EnrichmentResult(
        keywords=(),
        questions=(),
        alt_phrasings=(),
        model="claude-haiku-4-5",
        generated_at=_fixed_now().isoformat(),
    )
    captured = capsys.readouterr()
    assert captured.err.startswith("enrichment:")
    assert "invalid JSON" in captured.err


def test_non_zero_exit_yields_empty_result_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    enricher, _ = _make_enricher(
        RunResult(output="", exit_code=1, idle_timed_out=False),
    )

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert result.keywords == ()
    captured = capsys.readouterr()
    assert "enrichment:" in captured.err
    assert "status 1" in captured.err


def test_idle_timeout_yields_empty_result_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    enricher, _ = _make_enricher(
        RunResult(output="", exit_code=137, idle_timed_out=True),
    )

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert result.keywords == ()
    captured = capsys.readouterr()
    assert "idle timeout" in captured.err


def test_empty_output_yields_empty_result_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    enricher, _ = _make_enricher("   \n  ")

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert result.keywords == ()
    captured = capsys.readouterr()
    assert "empty output" in captured.err


def test_missing_keys_yield_empty_result_and_warn(
    capsys: pytest.CaptureFixture[str],
) -> None:
    enricher, _ = _make_enricher(json.dumps({"keywords": ["a"]}))

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert result.keywords == ()
    captured = capsys.readouterr()
    assert "invalid JSON" in captured.err


def test_wrong_value_types_yield_empty_result_and_warn(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {
        "keywords": ["ok"],
        "questions": "not a list",
        "alt_phrasings": ["also ok"],
    }
    enricher, _ = _make_enricher(json.dumps(payload))

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert result.keywords == ()
    captured = capsys.readouterr()
    assert "invalid JSON" in captured.err


def test_runner_exception_yields_empty_result_and_warns(
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

    enricher = ClaudeEnricher(runner=_RaisingRunner(), model="m", now=_fixed_now)

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert result.keywords == ()
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "boom" in captured.err


def test_keywords_clamped_to_max() -> None:
    keywords = [f"kw{i}" for i in range(MAX_KEYWORDS + 10)]
    payload = {
        "keywords": keywords,
        "questions": ["q1?"],
        "alt_phrasings": ["alt"],
    }
    enricher, _ = _make_enricher(json.dumps(payload))

    result = enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert len(result.keywords) == MAX_KEYWORDS
    assert result.keywords[0] == "kw0"
    assert result.keywords[-1] == f"kw{MAX_KEYWORDS - 1}"


def test_prompt_includes_title_heading_path_summary_and_body() -> None:
    payload = {"keywords": ["k"], "questions": ["q?"], "alt_phrasings": ["a"]}
    enricher, runner = _make_enricher(json.dumps(payload))

    enricher.enrich_chunk(
        title="My Document",
        heading_path=("Top", "Sub"),
        body="THE CHUNK BODY",
        summary="a short summary",
    )

    assert runner.last_prompt is not None
    prompt = runner.last_prompt
    assert "My Document" in prompt
    assert "Top / Sub" in prompt
    assert "a short summary" in prompt
    assert "THE CHUNK BODY" in prompt


def test_prompt_renders_placeholders_for_missing_summary_and_path() -> None:
    payload = {"keywords": ["k"], "questions": ["q?"], "alt_phrasings": ["a"]}
    enricher, runner = _make_enricher(json.dumps(payload))

    enricher.enrich_chunk(
        title="t", heading_path=(), body="b", summary=None
    )

    assert runner.last_prompt is not None
    assert "(none)" in runner.last_prompt


def test_enrichment_result_to_json_round_trip() -> None:
    original = EnrichmentResult(
        keywords=("a", "вебхук"),
        questions=("q?",),
        alt_phrasings=("alt",),
        model="m",
        generated_at="2026-05-06T12:30:00+00:00",
    )

    rebuilt = EnrichmentResult.from_json(
        original.to_json(),
        model=original.model,
        generated_at=original.generated_at,
    )

    assert rebuilt == original
