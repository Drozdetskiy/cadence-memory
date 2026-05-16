"""Tests for DefaultClaudeRunner, ClaudeResult, and ClaudeRunner Protocol."""

from __future__ import annotations

import inspect
from pathlib import Path

from cadence_memory.executor.events import ToolUseEvent
from cadence_memory.executor.runner import (
    ClaudeRunner,
    DefaultClaudeRunner,
)
from cadence_memory.executor.streaming_runner import RunResult, StreamingClaudeRunner
from cadence_memory.progress.logger import Logger, NullLogger, StdoutLogger

_NULL_LOGGER: Logger = NullLogger()


class FakeStreamingRunner(StreamingClaudeRunner):
    def __init__(self, result: RunResult) -> None:
        super().__init__()
        self._result = result
        self.captured_prompt: str | None = None
        self.captured_model: str | None = None
        self.captured_cwd: Path | None = None
        self.captured_allowed_tools: tuple[str, ...] | None = None
        self.captured_extra_args: tuple[str, ...] | None = None
        self.captured_idle_timeout_s: float | None = None
        self.captured_logger: Logger | None = None
        self.captured_phase: str | None = None
        self.captured_extra_kwargs: dict[str, object] = {}

    def run(  # type: ignore[override]
        self,
        prompt: str,
        *,
        model: str,
        cwd: Path | None = None,
        allowed_tools: tuple[str, ...] = (),
        extra_args: tuple[str, ...] = (),
        idle_timeout_s: float = 300.0,
        logger: Logger = _NULL_LOGGER,
        phase: str = "claude",
        **extra: object,
    ) -> RunResult:
        self.captured_prompt = prompt
        self.captured_model = model
        self.captured_cwd = cwd
        self.captured_allowed_tools = allowed_tools
        self.captured_extra_args = extra_args
        self.captured_idle_timeout_s = idle_timeout_s
        self.captured_logger = logger
        self.captured_phase = phase
        self.captured_extra_kwargs = dict(extra)
        return self._result


def _ok_result(
    *,
    exit_code: int = 0,
    final_text: str = "",
    tool_calls: tuple[ToolUseEvent, ...] = (),
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> RunResult:
    return RunResult(
        exit_code=exit_code,
        final_text=final_text,
        tool_calls=tool_calls,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        error=error,
    )


def test_default_runner_passes_no_extra_args() -> None:
    fake = FakeStreamingRunner(_ok_result())
    runner = DefaultClaudeRunner(streaming=fake)
    runner.run(
        prompt="hi",
        model="claude-opus-4-7",
        allowed_tools=(),
        idle_timeout_s=300,
    )
    assert fake.captured_extra_args == ()


def test_default_runner_propagates_model_tools_cwd() -> None:
    fake = FakeStreamingRunner(_ok_result())
    runner = DefaultClaudeRunner(streaming=fake)
    cwd = Path("/some/wiki")
    runner.run(
        prompt="do work",
        model="claude-sonnet-4-6",
        allowed_tools=("Read", "Write"),
        idle_timeout_s=120,
        cwd=cwd,
    )
    assert fake.captured_prompt == "do work"
    assert fake.captured_model == "claude-sonnet-4-6"
    assert fake.captured_allowed_tools == ("Read", "Write")
    assert fake.captured_cwd == cwd
    assert fake.captured_idle_timeout_s == 120


def test_default_runner_success_on_exit_zero() -> None:
    tool_calls = (ToolUseEvent(tool="Read", input_summary="/x"),)
    fake = FakeStreamingRunner(
        _ok_result(
            exit_code=0,
            final_text="ok",
            tool_calls=tool_calls,
            cost_usd=0.01,
            duration_ms=42,
            error=None,
        )
    )
    runner = DefaultClaudeRunner(streaming=fake)
    result = runner.run(
        prompt="p",
        model="m",
        allowed_tools=(),
        idle_timeout_s=300,
    )
    assert result.success is True
    assert result.final_text == "ok"
    assert result.tool_call_count == len(tool_calls)
    assert result.cost_usd == 0.01
    assert result.duration_ms == 42
    assert result.error is None


def test_default_runner_preserves_error_on_zero_exit() -> None:
    fake = FakeStreamingRunner(
        _ok_result(exit_code=0, final_text="partial", error="claude blew up")
    )
    runner = DefaultClaudeRunner(streaming=fake)
    result = runner.run(
        prompt="p",
        model="m",
        allowed_tools=(),
        idle_timeout_s=300,
    )
    assert result.success is True
    assert result.error == "claude blew up"
    assert result.final_text == "partial"


def test_default_runner_failure_on_nonzero_exit() -> None:
    fake = FakeStreamingRunner(
        _ok_result(
            exit_code=124,
            final_text="",
            tool_calls=(),
            cost_usd=None,
            duration_ms=None,
            error="idle watchdog timeout",
        )
    )
    runner = DefaultClaudeRunner(streaming=fake)
    result = runner.run(
        prompt="p",
        model="m",
        allowed_tools=(),
        idle_timeout_s=300,
    )
    assert result.success is False
    assert result.error == "idle watchdog timeout"
    assert result.cost_usd is None
    assert result.duration_ms is None
    assert result.tool_call_count == 0


def test_default_runner_tool_call_count_matches_streaming() -> None:
    tool_calls = (
        ToolUseEvent(tool="Read", input_summary="/a"),
        ToolUseEvent(tool="Write", input_summary="/b: hi"),
    )
    fake = FakeStreamingRunner(_ok_result(tool_calls=tool_calls))
    runner = DefaultClaudeRunner(streaming=fake)
    result = runner.run(
        prompt="p",
        model="m",
        allowed_tools=(),
        idle_timeout_s=300,
    )
    assert result.tool_call_count == 2


def test_default_runner_constructs_streaming_when_omitted() -> None:
    runner = DefaultClaudeRunner()
    assert isinstance(runner._streaming, StreamingClaudeRunner)


def test_default_runner_satisfies_protocol() -> None:
    runner: ClaudeRunner = DefaultClaudeRunner()
    protocol_params = {
        name: param
        for name, param in inspect.signature(ClaudeRunner.run).parameters.items()
        if name != "self"
    }
    impl_params = inspect.signature(runner.run).parameters
    assert list(protocol_params) == list(impl_params)
    for name, protocol_param in protocol_params.items():
        assert impl_params[name].kind == protocol_param.kind


def test_default_runner_forwards_null_logger_by_default() -> None:
    fake = FakeStreamingRunner(_ok_result())
    runner = DefaultClaudeRunner(streaming=fake)
    runner.run(prompt="hi", model="m", allowed_tools=(), idle_timeout_s=300)
    assert isinstance(fake.captured_logger, NullLogger)
    assert fake.captured_phase == "claude"


def test_default_runner_forwards_custom_logger_and_phase() -> None:
    logger = StdoutLogger()
    fake = FakeStreamingRunner(_ok_result())
    runner = DefaultClaudeRunner(streaming=fake)
    runner.run(
        prompt="hi",
        model="m",
        allowed_tools=(),
        idle_timeout_s=300,
        logger=logger,
        phase="bootstrap-stage",
    )
    assert fake.captured_logger is logger
    assert fake.captured_phase == "bootstrap-stage"
