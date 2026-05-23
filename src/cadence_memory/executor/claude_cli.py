"""Single source of truth for the `claude` subprocess CLI surface.

Every flag and environment-variable decision for the spawned `claude` process
lives here, not inline in the runner. Pinning the surface in one module means
an upstream Claude Code CLI change is caught by the contract test
(`tests/executor/test_claude_cli_contract.py`) on a clear, intentional
assertion instead of breaking a live ingest at runtime.

Before changing any constant in this module, review the upstream Claude Code
release notes to confirm the new flag/env contract:
https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: Static, value-independent flags passed to every `claude` invocation. The
#: prompt is piped via stdin as framed ``stream-json`` (``--input-format
#: stream-json``), so it is not an argv argument. ``--model``,
#: ``--allowedTools``, and any ``extra_args`` are appended dynamically by
#: :func:`build_claude_argv`.
CLAUDE_CLI_FLAGS: Final[tuple[str, ...]] = (
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "--input-format",
    "stream-json",
)

#: Environment variables stripped from the parent environment before spawning
#: `claude`. This is a deny-list: every other parent var passes through.
#: ``CLAUDECODE`` is removed so the child does not believe it is a nested
#: Claude Code session.
CLAUDE_ENV_STRIP: Final[frozenset[str]] = frozenset({"CLAUDECODE"})


def build_claude_argv(
    claude_command: str,
    *,
    model: str,
    allowed_tools: tuple[str, ...] = (),
    extra_args: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Build the full argv for a `claude` invocation.

    Order: the command, the pinned static flags, ``--model <model>``, then
    ``--allowedTools <csv>`` only when ``allowed_tools`` is non-empty, then any
    ``extra_args`` appended last.
    """
    argv: list[str] = [claude_command, *CLAUDE_CLI_FLAGS, "--model", model]
    if allowed_tools:
        argv.extend(["--allowedTools", ",".join(allowed_tools)])
    argv.extend(extra_args)
    return tuple(argv)


def build_claude_env(
    parent_env: Mapping[str, str],
    *,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the child environment from ``parent_env``.

    Copies ``parent_env`` excluding keys in :data:`CLAUDE_ENV_STRIP`, then
    applies ``overrides`` (override values win over the parent's).
    """
    env: dict[str, str] = {k: v for k, v in parent_env.items() if k not in CLAUDE_ENV_STRIP}
    if overrides:
        env.update(overrides)
    return env
