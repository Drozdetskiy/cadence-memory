"""Spawn an interactive Claude session pointed at the cadence-memory store."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, MutableMapping, Sequence
from pathlib import Path

__all__ = ["run_chat"]


def _default_spawn(cmd: Sequence[str], env: MutableMapping[str, str]) -> int:
    return subprocess.run(list(cmd), env=dict(env), check=False).returncode


def run_chat(
    *,
    store_dir: Path,
    extra_args: Sequence[str],
    env: MutableMapping[str, str],
    spawn: Callable[[Sequence[str], MutableMapping[str, str]], int] = _default_spawn,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    if which("claude") is None:
        print(
            "error: 'claude' is not on PATH; install Claude Code first",
            file=sys.stderr,
        )
        return 127

    env["CADENCE_MEMORY_DIR"] = str(store_dir.resolve())
    cmd: list[str] = ["claude", *extra_args]
    return spawn(cmd, env)
