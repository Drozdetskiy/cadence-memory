"""High-level Claude runner Protocol and default implementation (design2 §2.6, §6.1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cadence_memory.executor.streaming_runner import StreamingClaudeRunner
from cadence_memory.progress.logger import Logger, NullLogger

_NULL_LOGGER: Logger = NullLogger()


@dataclass(frozen=True, slots=True)
class ClaudeResult:
    success: bool
    final_text: str
    cost_usd: float | None
    duration_ms: int | None
    tool_call_count: int
    error: str | None


class ClaudeRunner(Protocol):
    def run(
        self,
        *,
        prompt: str,
        model: str,
        allowed_tools: tuple[str, ...],
        idle_timeout_s: int,
        cwd: Path | None = None,
        logger: Logger = _NULL_LOGGER,
        phase: str = "claude",
    ) -> ClaudeResult: ...


class DefaultClaudeRunner:
    def __init__(
        self,
        *,
        streaming: StreamingClaudeRunner | None = None,
    ) -> None:
        self._streaming = streaming if streaming is not None else StreamingClaudeRunner()

    def run(
        self,
        *,
        prompt: str,
        model: str,
        allowed_tools: tuple[str, ...],
        idle_timeout_s: int,
        cwd: Path | None = None,
        logger: Logger = _NULL_LOGGER,
        phase: str = "claude",
    ) -> ClaudeResult:
        raw = self._streaming.run(
            prompt,
            model=model,
            cwd=cwd,
            allowed_tools=allowed_tools,
            extra_args=(),
            idle_timeout_s=idle_timeout_s,
            logger=logger,
            phase=phase,
        )
        return ClaudeResult(
            success=(raw.exit_code == 0),
            final_text=raw.final_text,
            cost_usd=raw.cost_usd,
            duration_ms=raw.duration_ms,
            tool_call_count=len(raw.tool_calls),
            error=raw.error,
        )


__all__ = ["ClaudeResult", "ClaudeRunner", "DefaultClaudeRunner"]
