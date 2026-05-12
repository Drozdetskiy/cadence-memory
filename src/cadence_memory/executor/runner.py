"""High-level Claude runner Protocol and default implementation (design2 §2.6, §6.1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cadence_memory.executor.streaming_runner import StreamingClaudeRunner


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
        budget_usd: float | None,
        allowed_tools: tuple[str, ...],
        idle_timeout_s: int,
        cwd: Path | None = None,
    ) -> ClaudeResult: ...


class DefaultClaudeRunner:
    def __init__(
        self,
        *,
        streaming: StreamingClaudeRunner | None = None,
        bare: bool = True,
    ) -> None:
        self._streaming = streaming if streaming is not None else StreamingClaudeRunner()
        self._bare = bare

    def run(
        self,
        *,
        prompt: str,
        model: str,
        budget_usd: float | None,
        allowed_tools: tuple[str, ...],
        idle_timeout_s: int,
        cwd: Path | None = None,
    ) -> ClaudeResult:
        extra_args: tuple[str, ...] = ("--bare",) if self._bare else ()
        raw = self._streaming.run(
            prompt,
            model=model,
            cwd=cwd,
            allowed_tools=allowed_tools,
            budget_usd=budget_usd,
            extra_args=extra_args,
            idle_timeout_s=idle_timeout_s,
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
