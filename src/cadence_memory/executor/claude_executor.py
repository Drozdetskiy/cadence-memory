from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import IO, Protocol

from cadence_memory.executor.events import (
    AssistantEvent,
    ClaudeEvent,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ResultEvent,
    TextContent,
    TextDelta,
    ToolUseBlock,
    parse_event,
)
from cadence_memory.executor.process_group import ProcessGroupCleanup


class ClaudeNotFound(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunResult:
    output: str
    exit_code: int
    idle_timed_out: bool


class ClaudeRunner(Protocol):
    def run(self, prompt: str, *, env: Mapping[str, str] | None = None) -> RunResult: ...


def _extract_text_from_event(event: ClaudeEvent) -> str:
    match event:
        case AssistantEvent(message=msg) if msg is not None:
            return "".join(c.text for c in msg.content if isinstance(c, TextContent))
        case ContentBlockDeltaEvent(delta=TextDelta(text=t)):
            return t
        case ResultEvent(result=result) if result is not None and not isinstance(result, str):
            return result.output or ""
        case _:
            return ""


def filter_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = env if env is not None else os.environ
    out = dict(source)
    out.pop("ANTHROPIC_API_KEY", None)
    out.pop("CLAUDECODE", None)
    return out


class _IdleWatchdog:
    def __init__(self, timeout: float, on_idle: Callable[[], None]) -> None:
        self._timeout = timeout
        self._on_idle = on_idle
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self.triggered = threading.Event()

    def active(self) -> bool:
        return self._timeout > 0

    def reset(self) -> None:
        if not self.active():
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._timeout, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _fire(self) -> None:
        self.triggered.set()
        self._on_idle()


@dataclass
class _ProcessHandle:
    stdout: IO[str]
    wait: Callable[[], int]
    cleanup: ProcessGroupCleanup | None = None


def _launch_process(
    cmd: list[str],
    prompt: str,
    env: Mapping[str, str],
) -> tuple[_ProcessHandle | None, Exception | None]:
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        env=dict(env),
    )
    cleanup = ProcessGroupCleanup(process)

    try:
        if process.stdin is not None:
            process.stdin.write(prompt)
            process.stdin.close()
    except BrokenPipeError:
        cleanup.kill_process_group()
        cleanup.wait()
        return None, RuntimeError("failed to send prompt: broken pipe")

    assert process.stdout is not None
    return (
        _ProcessHandle(stdout=process.stdout, wait=cleanup.wait, cleanup=cleanup),
        None,
    )


@dataclass
class StreamingClaudeRunner:
    binary: str = "claude"
    extra_args: tuple[str, ...] = ()
    idle_timeout: float = 0
    output_handler: Callable[[str], None] | None = None
    activity_handler: Callable[[str], None] | None = None
    _active_cleanup: ProcessGroupCleanup | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def run(self, prompt: str, *, env: Mapping[str, str] | None = None) -> RunResult:
        if shutil.which(self.binary) is None:
            raise ClaudeNotFound(self.binary)

        cmd = self._build_command()
        merged_env = filter_env(env)
        handle, launch_err = _launch_process(cmd, prompt, merged_env)
        if handle is None:
            assert launch_err is not None
            raise launch_err

        self._active_cleanup = handle.cleanup

        def on_idle() -> None:
            if handle.cleanup is not None:
                handle.cleanup.kill_process_group()

        watchdog = _IdleWatchdog(self.idle_timeout, on_idle)
        try:
            output = self._parse_stream(handle, watchdog)
            exit_code = handle.wait()
        finally:
            self._active_cleanup = None

        return RunResult(
            output=output,
            exit_code=exit_code,
            idle_timed_out=watchdog.triggered.is_set(),
        )

    def cancel(self) -> None:
        cleanup = self._active_cleanup
        if cleanup is not None:
            cleanup.kill_process_group()

    def _parse_stream(
        self,
        handle: _ProcessHandle,
        watchdog: _IdleWatchdog,
    ) -> str:
        output_parts: list[str] = []
        last_output_text = ""

        watchdog.reset()

        try:
            for raw_line in handle.stdout:
                line = raw_line.rstrip("\n").rstrip("\r")
                watchdog.reset()

                try:
                    raw = json.loads(line)
                except ValueError:
                    output_parts.append(line + "\n")
                    if self.output_handler:
                        self.output_handler(line + "\n")
                    last_output_text = line + "\n"
                    continue

                event = parse_event(raw)
                if event is None:
                    continue

                last_output_text = self._handle_event(event, output_parts, last_output_text)

        except BaseException:
            if handle.cleanup is not None:
                handle.cleanup.kill_process_group()
                with contextlib.suppress(Exception):
                    handle.cleanup.wait()
            raise
        finally:
            watchdog.cancel()

        return "".join(output_parts)

    def _handle_event(
        self,
        event: ClaudeEvent,
        output_parts: list[str],
        last_output_text: str,
    ) -> str:
        if isinstance(event, AssistantEvent) and self.output_handler:
            if last_output_text and not last_output_text.endswith("\n"):
                self.output_handler("\n")
            last_output_text = ""

        if (
            self.activity_handler
            and isinstance(event, ContentBlockStartEvent)
            and isinstance(event.content_block, ToolUseBlock)
        ):
            self.activity_handler(event.content_block.name)

        text = _extract_text_from_event(event)
        if text:
            output_parts.append(text)
            last_output_text = text
            if self.output_handler:
                self.output_handler(text)

        return last_output_text

    def _build_command(self) -> list[str]:
        cmd = [self.binary]
        if self.extra_args:
            cmd.extend(self.extra_args)
        else:
            cmd.extend(["--dangerously-skip-permissions", "--verbose"])
        cmd.extend(["--output-format", "stream-json", "--print"])
        return cmd
