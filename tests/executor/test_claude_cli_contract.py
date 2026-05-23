"""Contract tests pinning the `claude` subprocess CLI surface.

These snapshot the exact flags and env policy sent to the spawned `claude`
process. When upstream Claude Code changes its CLI, these fail with a clear,
intentional assertion instead of a live ingest breaking at runtime.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from cadence_memory.executor.claude_cli import (
    CLAUDE_CLI_FLAGS,
    CLAUDE_ENV_STRIP,
    build_claude_argv,
    build_claude_env,
)

_UPSTREAM_HINT = (
    "Claude subprocess CLI surface changed. The likely cause is an upstream "
    "Claude Code CLI change; review the release notes "
    "(https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) "
    "before updating this constant."
)


def test_claude_cli_flags_pinned() -> None:
    assert CLAUDE_CLI_FLAGS == (
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--input-format",
        "stream-json",
    ), _UPSTREAM_HINT


def test_claude_cli_env_strip_pinned() -> None:
    assert frozenset({"CLAUDECODE"}) == CLAUDE_ENV_STRIP, _UPSTREAM_HINT


def test_claude_cli_argv_construction() -> None:
    argv = build_claude_argv(
        "claude",
        model="claude-sonnet-4-6",
        allowed_tools=("Read", "Write", "Edit"),
        extra_args=("--foo", "bar"),
    )
    assert argv == (
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--input-format",
        "stream-json",
        "--model",
        "claude-sonnet-4-6",
        "--allowedTools",
        "Read,Write,Edit",
        "--foo",
        "bar",
    )


def test_claude_cli_argv_omits_allowed_tools_when_empty() -> None:
    argv = build_claude_argv("claude", model="m", allowed_tools=())
    assert "--allowedTools" not in argv
    # extra_args still trail; with none, --model <model> is last.
    assert argv[-2:] == ("--model", "m")


def test_claude_cli_env_strips_claudecode_without_overrides() -> None:
    parent = {"CLAUDECODE": "1", "WEIRD_VAR_FOR_TEST": "sentinel"}
    env = build_claude_env(parent)

    assert "CLAUDECODE" not in env
    assert env["WEIRD_VAR_FOR_TEST"] == "sentinel"


def test_claude_cli_env_strips_claudecode() -> None:
    parent = {
        "CLAUDECODE": "1",
        "WEIRD_VAR_FOR_TEST": "sentinel",
        "ANTHROPIC_API_KEY": "sk-parent",
    }
    env = build_claude_env(parent, overrides={"ANTHROPIC_API_KEY": "sk-override"})

    assert "CLAUDECODE" not in env
    assert env["WEIRD_VAR_FOR_TEST"] == "sentinel"
    assert env["ANTHROPIC_API_KEY"] == "sk-override"


@pytest.mark.skipif(shutil.which("claude") is None, reason="no real claude binary installed")
def test_claude_cli_help_includes_required_flags() -> None:
    proc = subprocess.run(
        ["claude", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"`claude --help` exited {proc.returncode}. {_UPSTREAM_HINT}"
    output = proc.stdout + proc.stderr
    for flag in ("-p", "--output-format", "--verbose", "--input-format"):
        assert flag in output, f"{flag!r} missing from `claude --help`. {_UPSTREAM_HINT}"
