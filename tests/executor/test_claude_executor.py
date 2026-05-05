from __future__ import annotations

import contextlib
import io
import json
import os
import threading
from collections.abc import Callable
from typing import IO

import pytest

from cadence_memory.executor import claude_executor as ce_module
from cadence_memory.executor.claude_executor import (
    ClaudeNotFound,
    StreamingClaudeRunner,
    _ProcessHandle,
    filter_env,
)


class _FakeCleanup:
    def __init__(self, on_kill: Callable[[], None] | None = None) -> None:
        self.killed = False
        self._on_kill = on_kill

    def kill_process_group(self) -> None:
        self.killed = True
        if self._on_kill is not None:
            self._on_kill()


def _stdout_from_lines(lines: list[str]) -> IO[str]:
    body = "".join(line + "\n" for line in lines)
    return io.StringIO(body)


def _make_handle(
    stdout: IO[str],
    *,
    exit_code: int = 0,
    cleanup: _FakeCleanup | None = None,
    wait_event: threading.Event | None = None,
) -> _ProcessHandle:
    def wait() -> int:
        if wait_event is not None:
            wait_event.wait()
        return exit_code

    return _ProcessHandle(stdout=stdout, wait=wait, cleanup=cleanup)  # type: ignore[arg-type]


def _patch_launch(
    monkeypatch: pytest.MonkeyPatch,
    handle: _ProcessHandle,
    *,
    recorder: list[dict[str, object]] | None = None,
) -> None:
    def fake_launch(
        cmd: list[str],
        prompt: str,
        env: dict[str, str],
    ) -> tuple[_ProcessHandle | None, Exception | None]:
        if recorder is not None:
            recorder.append({"cmd": cmd, "prompt": prompt, "env": dict(env)})
        return handle, None

    monkeypatch.setattr(ce_module, "_launch_process", fake_launch)
    monkeypatch.setattr(
        "cadence_memory.executor.claude_executor.shutil.which",
        lambda _name: "/fake/claude",
    )


def test_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}
        ),
        json.dumps(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}}
        ),
        json.dumps({"type": "result", "result": {"output": "!"}}),
    ]
    handle = _make_handle(_stdout_from_lines(lines), exit_code=0)
    _patch_launch(monkeypatch, handle)

    chunks: list[str] = []
    runner = StreamingClaudeRunner(output_handler=chunks.append)
    result = runner.run("prompt")

    assert result.output == "hello world!"
    assert result.exit_code == 0
    assert result.idle_timed_out is False
    assert chunks == ["hello", " world", "!"]


def test_activity_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read"},
            }
        ),
    ]
    handle = _make_handle(_stdout_from_lines(lines))
    _patch_launch(monkeypatch, handle)

    activity: list[str] = []
    runner = StreamingClaudeRunner(activity_handler=activity.append)
    runner.run("prompt")

    assert activity == ["Read"]


def test_non_json_line_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = io.StringIO("not json line\n")
    handle = _make_handle(raw)
    _patch_launch(monkeypatch, handle)

    chunks: list[str] = []
    runner = StreamingClaudeRunner(output_handler=chunks.append)
    result = runner.run("prompt")

    assert chunks == ["not json line\n"]
    assert result.output == "not json line\n"


def test_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    r_fd, w_fd = os.pipe()
    reader = os.fdopen(r_fd, "r")
    writer = os.fdopen(w_fd, "w")

    wait_event = threading.Event()

    def on_kill() -> None:
        with contextlib.suppress(Exception):
            writer.close()
        wait_event.set()

    cleanup = _FakeCleanup(on_kill=on_kill)
    handle = _make_handle(reader, exit_code=0, cleanup=cleanup, wait_event=wait_event)
    _patch_launch(monkeypatch, handle)

    runner = StreamingClaudeRunner(idle_timeout=0.05)
    result = runner.run("prompt")

    assert result.idle_timed_out is True
    assert cleanup.killed is True


def test_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    r_fd, w_fd = os.pipe()
    reader = os.fdopen(r_fd, "r")
    writer = os.fdopen(w_fd, "w")

    wait_event = threading.Event()

    def on_kill() -> None:
        with contextlib.suppress(Exception):
            writer.close()
        wait_event.set()

    cleanup = _FakeCleanup(on_kill=on_kill)
    handle = _make_handle(reader, exit_code=0, cleanup=cleanup, wait_event=wait_event)
    _patch_launch(monkeypatch, handle)

    runner = StreamingClaudeRunner()
    result_holder: list[object] = []

    def go() -> None:
        result_holder.append(runner.run("prompt"))

    t = threading.Thread(target=go)
    t.start()

    for _ in range(200):
        if runner._active_cleanup is not None:
            break
        threading.Event().wait(0.01)

    runner.cancel()
    t.join(timeout=2.0)

    assert not t.is_alive()
    assert cleanup.killed is True


def test_stream_exception_kills_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BoomStream(io.StringIO):
        def __next__(self) -> str:
            raise RuntimeError("boom")

    cleanup = _FakeCleanup()
    handle = _make_handle(_BoomStream(), cleanup=cleanup)
    _patch_launch(monkeypatch, handle)

    runner = StreamingClaudeRunner()
    with pytest.raises(RuntimeError, match="boom"):
        runner.run("prompt")

    assert cleanup.killed is True


def test_claude_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cadence_memory.executor.claude_executor.shutil.which", lambda _name: None)
    runner = StreamingClaudeRunner(binary="missing-claude")
    with pytest.raises(ClaudeNotFound):
        runner.run("prompt")


def test_launch_error_is_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = RuntimeError("failed to send prompt: broken pipe")

    def fake_launch(
        cmd: list[str],
        prompt: str,
        env: dict[str, str],
    ) -> tuple[_ProcessHandle | None, Exception | None]:
        return None, sentinel

    monkeypatch.setattr(ce_module, "_launch_process", fake_launch)
    monkeypatch.setattr(
        "cadence_memory.executor.claude_executor.shutil.which",
        lambda _name: "/fake/claude",
    )

    runner = StreamingClaudeRunner()
    with pytest.raises(RuntimeError) as excinfo:
        runner.run("prompt")
    assert excinfo.value is sentinel


def test_filter_env_strips_secrets() -> None:
    out = filter_env({"ANTHROPIC_API_KEY": "x", "CLAUDECODE": "1", "FOO": "bar"})
    assert out == {"FOO": "bar"}


def test_filter_env_passed_to_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = _make_handle(_stdout_from_lines([]))
    recorder: list[dict[str, object]] = []
    _patch_launch(monkeypatch, handle, recorder=recorder)

    runner = StreamingClaudeRunner()
    runner.run("prompt", env={"ANTHROPIC_API_KEY": "x", "CLAUDECODE": "1", "KEEP": "y"})

    assert len(recorder) == 1
    captured = recorder[0]["env"]
    assert isinstance(captured, dict)
    assert captured.get("KEEP") == "y"
    assert "ANTHROPIC_API_KEY" not in captured
    assert "CLAUDECODE" not in captured


def test_build_command_defaults() -> None:
    runner = StreamingClaudeRunner()
    assert runner._build_command() == [
        "claude",
        "--dangerously-skip-permissions",
        "--verbose",
        "--output-format",
        "stream-json",
        "--print",
    ]


def test_build_command_extra_args_replaces_defaults() -> None:
    runner = StreamingClaudeRunner(extra_args=("--foo", "bar"))
    assert runner._build_command() == [
        "claude",
        "--foo",
        "bar",
        "--output-format",
        "stream-json",
        "--print",
    ]
