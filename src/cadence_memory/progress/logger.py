"""Logger Protocol and implementations for cadence-memory progress output (design2 §2)."""

from __future__ import annotations

import json
import os
import sys
from typing import Literal, Protocol, assert_never

from cadence_memory.progress.events import (
    ClaudeProgressEvent,
    ErrorEvent,
    IngestEndEvent,
    IngestStartEvent,
    PhaseEndEvent,
    PhaseStartEvent,
    ProgressEvent,
    StageEndEvent,
    StageStartEvent,
    now_ts,
)

Level = Literal["debug", "info", "warn", "error"]
ColorMode = Literal["auto", "always", "never"]

_LEVEL_ORDER: dict[Level, int] = {"debug": 0, "info": 1, "warn": 2, "error": 3}

_ANSI_DIM = "\033[2m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_RESET = "\033[0m"


class Logger(Protocol):
    def print(self, fmt: str, *args: object) -> None: ...
    def info(self, fmt: str, *args: object) -> None: ...
    def warn(self, fmt: str, *args: object) -> None: ...
    def error(self, fmt: str, *args: object) -> None: ...
    def section(self, label: str) -> None: ...
    def log_event(self, event: ProgressEvent) -> None: ...

    @property
    def path(self) -> str | None: ...


def _format_event_summary(event: ProgressEvent) -> str:
    if isinstance(event, PhaseStartEvent):
        extras: list[str] = []
        if event.repo:
            extras.append(f"repo={event.repo}")
        if event.stage:
            extras.append(f"stage={event.stage}")
        if event.model:
            extras.append(f"model={event.model}")
        if event.source:
            extras.append(f"source={event.source}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        return f"phase {event.phase} starting{suffix}"
    elif isinstance(event, PhaseEndEvent):
        parts: list[str] = [f"phase {event.phase} done"]
        if event.duration_ms is not None:
            parts.append(f"{event.duration_ms}ms")
        if event.result:
            parts.append(f"result={event.result}")
        if event.cost_usd_estimate is not None:
            parts.append(f"${event.cost_usd_estimate:.4f}")
        return " ".join(parts)
    elif isinstance(event, StageStartEvent):
        return f"stage {event.stage_index}: {event.stage_name} ({event.repo})"
    elif isinstance(event, StageEndEvent):
        stage_parts: list[str] = [f"stage {event.stage_index} done"]
        if event.duration_ms is not None:
            stage_parts.append(f"{event.duration_ms}ms")
        if event.result:
            stage_parts.append(f"result={event.result}")
        if event.pages_touched is not None:
            stage_parts.append(f"pages={event.pages_touched}")
        return " ".join(stage_parts)
    elif isinstance(event, IngestStartEvent):
        return f"ingest {event.repo}@{event.commit_sha}: {event.subject}"
    elif isinstance(event, IngestEndEvent):
        ingest_parts: list[str] = [f"ingest {event.repo}@{event.commit_sha} done"]
        if event.duration_ms is not None:
            ingest_parts.append(f"{event.duration_ms}ms")
        if event.result:
            ingest_parts.append(f"result={event.result}")
        return " ".join(ingest_parts)
    elif isinstance(event, ClaudeProgressEvent):
        detail = f": {event.detail}" if event.detail else ""
        return f"[{event.phase}] {event.kind}{detail}"
    elif isinstance(event, ErrorEvent):
        detail = f" ({event.detail})" if event.detail else ""
        return f"error in {event.phase}: {event.message}{detail}"
    else:
        assert_never(event)


class StdoutLogger:
    def __init__(
        self,
        level: Level = "info",
        color: ColorMode = "auto",
        jsonl_path: str | None = None,
    ) -> None:
        self._level = level
        self._color = color
        self._jsonl_path = jsonl_path

    @property
    def path(self) -> str | None:
        return self._jsonl_path

    def _use_color(self) -> bool:
        if self._color == "always":
            return True
        if self._color == "never":
            return False
        return sys.stdout.isatty()

    def _should_emit(self, level: Level) -> bool:
        return _LEVEL_ORDER[level] >= _LEVEL_ORDER[self._level]

    def _format_line(self, category: str, message: str) -> str:
        ts = now_ts()
        if self._use_color():
            if category == "ERROR":
                return f"{_ANSI_RED}[{ts}] [{category}] {message}{_ANSI_RESET}"
            if category == "WARN":
                return f"{_ANSI_YELLOW}[{ts}] [{category}] {message}{_ANSI_RESET}"
            return f"{_ANSI_DIM}[{ts}]{_ANSI_RESET} [{category}] {message}"
        return f"[{ts}] [{category}] {message}"

    def print(self, fmt: str, *args: object) -> None:
        message = fmt % args if args else fmt
        sys.stdout.write(message + "\n")
        sys.stdout.flush()

    def info(self, fmt: str, *args: object) -> None:
        if not self._should_emit("info"):
            return
        message = fmt % args if args else fmt
        sys.stdout.write(self._format_line("INFO", message) + "\n")
        sys.stdout.flush()

    def warn(self, fmt: str, *args: object) -> None:
        if not self._should_emit("warn"):
            return
        message = fmt % args if args else fmt
        sys.stderr.write(self._format_line("WARN", message) + "\n")
        sys.stderr.flush()

    def error(self, fmt: str, *args: object) -> None:
        if not self._should_emit("error"):
            return
        message = fmt % args if args else fmt
        sys.stderr.write(self._format_line("ERROR", message) + "\n")
        sys.stderr.flush()

    def section(self, label: str) -> None:
        sys.stdout.write(f"\n=== {label} ===\n")
        sys.stdout.flush()

    def log_event(self, event: ProgressEvent) -> None:
        summary = _format_event_summary(event)
        if isinstance(event, ErrorEvent):
            self.error("%s", summary)
        else:
            self.info("%s", summary)
        if self._jsonl_path is not None:
            self._write_jsonl(event, self._jsonl_path)

    def _write_jsonl(self, event: ProgressEvent, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_jsonl_dict()) + "\n")


class NullLogger:
    @property
    def path(self) -> str | None:
        return None

    def print(self, fmt: str, *args: object) -> None:
        pass

    def info(self, fmt: str, *args: object) -> None:
        pass

    def warn(self, fmt: str, *args: object) -> None:
        pass

    def error(self, fmt: str, *args: object) -> None:
        pass

    def section(self, label: str) -> None:
        pass

    def log_event(self, event: ProgressEvent) -> None:
        pass


__all__ = [
    "ColorMode",
    "Level",
    "Logger",
    "NullLogger",
    "StdoutLogger",
    "_format_event_summary",
]
