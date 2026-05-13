"""Tests for StreamingClaudeRunner using a fake subprocess.Popen harness."""

from __future__ import annotations

import inspect
import io
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import cast

import pytest

from cadence_memory.executor.streaming_runner import (
    RunResult,
    StreamingClaudeRunner,
)


class _FakeStdin:
    def __init__(self) -> None:
        self._buf = io.BytesIO()
        self.captured: bytes = b""
        self.close_called = False

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.captured = self._buf.getvalue()
        self.close_called = True


class _FakeStream:
    """File-like with .readline() returning canned bytes lines, then EOF.

    delay_before_eof_s makes the final empty-bytes return block, used to
    simulate a hung child for the watchdog test.
    """

    def __init__(self, lines: list[bytes], delay_before_eof_s: float = 0.0) -> None:
        self._lines = list(lines)
        self._delay_before_eof_s = delay_before_eof_s
        self._delayed = False

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._delay_before_eof_s and not self._delayed:
            self._delayed = True
            time.sleep(self._delay_before_eof_s)
        return b""


class FakePopen:
    def __init__(
        self,
        *,
        stdout_lines: list[bytes] | None = None,
        stderr_lines: list[bytes] | None = None,
        returncode: int = 0,
        stdout_delay_before_eof_s: float = 0.0,
    ) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStream(stdout_lines or [], stdout_delay_before_eof_s)
        self.stderr = _FakeStream(stderr_lines or [])
        # Pid that almost certainly doesn't exist -> ProcessGroupCleanup no-ops.
        self.pid = 999_999_999
        self.returncode: int | None = None
        self._final_returncode = returncode
        self.terminate_called = False
        self.kill_called = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._final_returncode
        return self._final_returncode

    def terminate(self) -> None:
        self.terminate_called = True
        self.returncode = -15

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9


class _FakeRunner(StreamingClaudeRunner):
    def __init__(self, fake: FakePopen, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._fake = fake
        self.captured_argv: list[str] | None = None
        self.captured_cwd: Path | None = None
        self.captured_env: dict[str, str] | None = None

    def _launch_process(
        self,
        argv: list[str],
        cwd: Path | None,
        env: dict[str, str],
    ) -> subprocess.Popen[bytes]:
        self.captured_argv = list(argv)
        self.captured_cwd = cwd
        self.captured_env = dict(env)
        return cast(subprocess.Popen[bytes], self._fake)


def _jsonl(obj: dict[str, object]) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def test_runner_happy_path() -> None:
    stdout_lines = [
        _jsonl({"type": "system", "subtype": "init", "session_id": "s1"}),
        _jsonl(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hello "}]},
            }
        ),
        _jsonl(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
            }
        ),
        _jsonl(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read", "input": {"path": "/x"}}],
                },
            }
        ),
        _jsonl(
            {
                "type": "result",
                "subtype": "success",
                "total_cost_usd": 0.01,
                "num_turns": 1,
                "duration_ms": 1234,
            }
        ),
    ]
    fake = FakePopen(stdout_lines=stdout_lines, returncode=0)
    runner = _FakeRunner(fake)

    result = runner.run(prompt="hi", model="claude-sonnet-4-6")

    assert isinstance(result, RunResult)
    assert result.exit_code == 0
    assert result.final_text == "hello world"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool == "Read"
    assert result.tool_calls[0].input_summary == "/x"
    assert result.cost_usd == 0.01
    assert result.duration_ms == 1234
    assert result.error is None


def test_runner_idle_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePopen(stdout_lines=[], stdout_delay_before_eof_s=2.0)
    killpg_calls: list[tuple[int, int]] = []

    def fake_getpgid(pid: int) -> int:
        return 12345

    def fake_killpg(pgid: int, sig: int) -> None:
        killpg_calls.append((pgid, sig))
        fake.returncode = -15

    monkeypatch.setattr(os, "getpgid", fake_getpgid)
    monkeypatch.setattr(os, "killpg", fake_killpg)

    runner = _FakeRunner(fake)

    start = time.monotonic()
    result = runner.run(prompt="hi", model="m", idle_timeout_s=0.1)
    elapsed = time.monotonic() - start

    assert result.exit_code == 124
    assert result.error == "idle watchdog timeout"
    assert result.cost_usd is None
    assert result.duration_ms is None
    # Watchdog fired well before the canned 2s EOF delay.
    assert elapsed < 1.0
    # Verify ProcessGroupCleanup was triggered when the watchdog fired.
    assert any(sig == signal.SIGTERM for _, sig in killpg_calls)


def test_runner_passes_budget_flag() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="x", model="m", budget_usd=0.50)

    assert runner.captured_argv is not None
    argv = runner.captured_argv
    idx = argv.index("--max-budget-usd")
    assert argv[idx + 1] == "0.5"


def test_runner_passes_allowed_tools() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="x", model="m", allowed_tools=("Read", "Write", "Edit"))

    assert runner.captured_argv is not None
    argv = runner.captured_argv
    idx = argv.index("--allowedTools")
    assert argv[idx + 1] == "Read,Write,Edit"


def test_runner_omits_budget_when_none() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="x", model="m", budget_usd=None)

    assert runner.captured_argv is not None
    assert "--max-budget-usd" not in runner.captured_argv


def test_runner_omits_allowed_tools_when_empty() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="x", model="m", allowed_tools=())

    assert runner.captured_argv is not None
    assert "--allowedTools" not in runner.captured_argv


def test_runner_extra_args_appended_last() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(
        prompt="x",
        model="m",
        allowed_tools=("Read",),
        budget_usd=0.5,
        extra_args=("--foo", "bar"),
    )

    assert runner.captured_argv is not None
    argv = runner.captured_argv
    assert argv[-2:] == ["--foo", "bar"]


def test_runner_filters_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEIRD_VAR_FOR_TEST", "sentinel-value")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_FOO", "bar")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="x", model="m")

    assert runner.captured_env is not None
    # Deny-list: arbitrary parent vars pass through.
    assert runner.captured_env.get("WEIRD_VAR_FOR_TEST") == "sentinel-value"
    # CLAUDE_CODE_* session vars pass through (Claude Code relies on them).
    assert runner.captured_env.get("CLAUDE_CODE_SESSION_FOO") == "bar"
    # Only CLAUDECODE is stripped so the child doesn't think it's nested.
    assert "CLAUDECODE" not in runner.captured_env
    # ANTHROPIC_API_KEY passes through for CI auth.
    assert runner.captured_env.get("ANTHROPIC_API_KEY") == "sk-test"


def test_runner_env_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-parent")
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake, env_overrides={"ANTHROPIC_API_KEY": "sk-override"})

    runner.run(prompt="x", model="m")

    assert runner.captured_env is not None
    assert runner.captured_env["ANTHROPIC_API_KEY"] == "sk-override"


def test_runner_prompt_written_to_stdin() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="hello there", model="m")

    assert fake.stdin.close_called is True
    payload = fake.stdin.captured.decode("utf-8")
    assert payload.endswith("\n")
    obj = json.loads(payload.rstrip("\n"))
    assert obj["type"] == "user"
    assert obj["message"]["role"] == "user"
    assert obj["message"]["content"] == [{"type": "text", "text": "hello there"}]


def test_runner_nonzero_exit_populates_error() -> None:
    fake = FakePopen(
        stdout_lines=[],
        stderr_lines=[b"boom\n", b"last error line\n"],
        returncode=1,
    )
    runner = _FakeRunner(fake)

    result = runner.run(prompt="x", model="m")

    assert result.exit_code == 1
    assert result.error == "last error line"


def test_runner_error_event_populates_error() -> None:
    stdout_lines = [
        _jsonl({"type": "error", "message": "claude blew up"}),
    ]
    fake = FakePopen(stdout_lines=stdout_lines, returncode=0)
    runner = _FakeRunner(fake)

    result = runner.run(prompt="x", model="m")

    assert result.exit_code == 0
    assert result.error == "claude blew up"


def test_runner_no_real_claude() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake, claude_command="this-binary-does-not-exist")

    result = runner.run(prompt="x", model="m")

    assert result.exit_code == 0
    assert runner.captured_argv is not None
    assert runner.captured_argv[0] == "this-binary-does-not-exist"


def test_runner_no_bare_when_extra_args_empty() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="x", model="m", extra_args=())

    assert runner.captured_argv is not None
    assert "--bare" not in runner.captured_argv


def test_runner_bare_when_passed_via_extra_args() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="x", model="m", extra_args=("--bare",))

    assert runner.captured_argv is not None
    assert "--bare" in runner.captured_argv


def test_runner_default_idle_timeout_used_when_omitted() -> None:
    sig = inspect.signature(StreamingClaudeRunner.run)
    assert sig.parameters["idle_timeout_s"].default == 300.0

    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    result = runner.run(prompt="x", model="m")

    assert result.exit_code == 0


def test_argv_contains_verbose_after_output_format() -> None:
    fake = FakePopen(returncode=0)
    runner = _FakeRunner(fake)

    runner.run(prompt="x", model="m")

    assert runner.captured_argv is not None
    argv = runner.captured_argv
    idx = argv.index("--output-format")
    assert argv[idx : idx + 3] == ["--output-format", "stream-json", "--verbose"]


def test_runresult_success_true_when_exit_code_zero() -> None:
    result = RunResult(
        exit_code=0,
        final_text="ok",
        tool_calls=(),
        cost_usd=None,
        duration_ms=None,
        error=None,
    )
    assert result.success is True


def test_runresult_success_false_when_exit_code_nonzero() -> None:
    result_one = RunResult(
        exit_code=1,
        final_text="",
        tool_calls=(),
        cost_usd=None,
        duration_ms=None,
        error="some error",
    )
    result_watchdog = RunResult(
        exit_code=124,
        final_text="",
        tool_calls=(),
        cost_usd=None,
        duration_ms=None,
        error="idle watchdog timeout",
    )
    assert result_one.success is False
    assert result_watchdog.success is False
