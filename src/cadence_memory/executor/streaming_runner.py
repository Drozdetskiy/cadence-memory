"""Streaming subprocess wrapper for `claude -p --output-format stream-json` (design2 §6.1)."""

from __future__ import annotations

import collections
import contextlib
import json
import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from cadence_memory.executor.events import (
    AssistantTextEvent,
    ErrorEvent,
    ResultEvent,
    ToolUseEvent,
    parse_event,
)
from cadence_memory.executor.process_group import ProcessGroupCleanup

_DEFAULT_ENV_PASSTHROUGH: tuple[str, ...] = ("PATH", "HOME", "USER", "ANTHROPIC_API_KEY")
_STDERR_BUFFER_LINES = 200
_STDERR_JOIN_TIMEOUT_S = 1.0
_PROC_WAIT_TIMEOUT_S = 5.0
_WATCHDOG_EXIT_CODE = 124
_WATCHDOG_ERROR_MESSAGE = "idle watchdog timeout"


@dataclass(frozen=True, slots=True)
class RunResult:
    exit_code: int
    final_text: str
    tool_calls: tuple[ToolUseEvent, ...]
    cost_usd: float | None
    duration_ms: int | None
    error: str | None

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class StreamingClaudeRunner:
    def __init__(
        self,
        *,
        claude_command: str = "claude",
        env_overrides: dict[str, str] | None = None,
        env_passthrough: tuple[str, ...] = _DEFAULT_ENV_PASSTHROUGH,
    ) -> None:
        self._claude_command = claude_command
        self._env_overrides = dict(env_overrides) if env_overrides else {}
        self._env_passthrough = tuple(env_passthrough)

    def run(
        self,
        prompt: str,
        *,
        model: str,
        cwd: Path | None = None,
        allowed_tools: tuple[str, ...] = (),
        budget_usd: float | None = None,
        extra_args: tuple[str, ...] = (),
        idle_timeout_s: float = 300.0,
    ) -> RunResult:
        argv = self._build_argv(
            model=model,
            allowed_tools=allowed_tools,
            budget_usd=budget_usd,
            extra_args=extra_args,
        )
        env = self._build_env()
        proc = self._launch_process(argv, cwd, env)

        final_text_parts: list[str] = []
        tool_calls: list[ToolUseEvent] = []
        cost_usd: float | None = None
        duration_ms: int | None = None
        error_message: str | None = None
        watchdog_fired = False

        with ProcessGroupCleanup(proc):
            stdin = proc.stdin
            assert stdin is not None
            framed = json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    },
                }
            )
            with contextlib.suppress(BrokenPipeError, OSError):
                stdin.write((framed + "\n").encode("utf-8"))
            with contextlib.suppress(BrokenPipeError, OSError):
                stdin.close()

            stdout_q: queue.Queue[str | None] = queue.Queue()
            stderr_lines: collections.deque[str] = collections.deque(maxlen=_STDERR_BUFFER_LINES)

            stdout_thread = threading.Thread(
                target=_drain_stdout, args=(proc, stdout_q), daemon=True
            )
            stderr_thread = threading.Thread(
                target=_drain_stderr, args=(proc, stderr_lines), daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()

            while True:
                try:
                    item = stdout_q.get(timeout=idle_timeout_s)
                except queue.Empty:
                    watchdog_fired = True
                    break
                if item is None:
                    break
                evt = parse_event(item)
                if evt is None:
                    continue
                if isinstance(evt, AssistantTextEvent):
                    final_text_parts.append(evt.text)
                elif isinstance(evt, ToolUseEvent):
                    tool_calls.append(evt)
                elif isinstance(evt, ResultEvent):
                    cost_usd = evt.total_cost_usd
                    duration_ms = evt.duration_ms
                elif isinstance(evt, ErrorEvent):
                    error_message = evt.message

            if watchdog_fired:
                return RunResult(
                    exit_code=_WATCHDOG_EXIT_CODE,
                    final_text="".join(final_text_parts),
                    tool_calls=tuple(tool_calls),
                    cost_usd=None,
                    duration_ms=None,
                    error=_WATCHDOG_ERROR_MESSAGE,
                )

            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_PROC_WAIT_TIMEOUT_S)
            stderr_thread.join(timeout=_STDERR_JOIN_TIMEOUT_S)

            exit_code = proc.returncode if proc.returncode is not None else -1
            if error_message is not None:
                error: str | None = error_message
            elif exit_code != 0 and stderr_lines:
                error = stderr_lines[-1]
            else:
                error = None

            return RunResult(
                exit_code=exit_code,
                final_text="".join(final_text_parts),
                tool_calls=tuple(tool_calls),
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                error=error,
            )

    def _build_argv(
        self,
        *,
        model: str,
        allowed_tools: tuple[str, ...],
        budget_usd: float | None,
        extra_args: tuple[str, ...],
    ) -> list[str]:
        argv: list[str] = [
            self._claude_command,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--input-format",
            "stream-json",
            "--model",
            model,
        ]
        if budget_usd is not None:
            argv.extend(["--max-budget-usd", f"{budget_usd}"])
        if allowed_tools:
            argv.extend(["--allowedTools", ",".join(allowed_tools)])
        argv.extend(extra_args)
        return argv

    def _build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for key in self._env_passthrough:
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        env.update(self._env_overrides)
        return env

    def _launch_process(
        self,
        argv: list[str],
        cwd: Path | None,
        env: dict[str, str],
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=(os.name != "nt"),
        )


def _drain_stdout(
    proc: subprocess.Popen[bytes],
    out_q: queue.Queue[str | None],
) -> None:
    stdout = proc.stdout
    if stdout is None:
        out_q.put(None)
        return
    try:
        while True:
            line = stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            out_q.put(decoded)
    finally:
        out_q.put(None)


def _drain_stderr(
    proc: subprocess.Popen[bytes],
    sink: collections.deque[str],
) -> None:
    stderr = proc.stderr
    if stderr is None:
        return
    while True:
        line = stderr.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if decoded:
            sink.append(decoded)
