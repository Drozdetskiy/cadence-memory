"""Unit tests for cadence_memory.chat.run_chat."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from pathlib import Path

import pytest

from cadence_memory.chat import run_chat


def _ok_which(_cmd: str) -> str | None:
    return "/usr/local/bin/claude"


def _missing_which(_cmd: str) -> str | None:
    return None


def test_injects_resolved_store_dir(tmp_path: Path) -> None:
    captured: dict[str, MutableMapping[str, str]] = {}

    def spawn(_cmd: Sequence[str], env: MutableMapping[str, str]) -> int:
        captured["env"] = env
        return 0

    env: dict[str, str] = {}
    run_chat(
        store_dir=tmp_path,
        extra_args=(),
        env=env,
        spawn=spawn,
        which=_ok_which,
    )
    assert captured["env"]["CADENCE_MEMORY_DIR"] == str(tmp_path.resolve())


def test_forwards_extra_args_verbatim(tmp_path: Path) -> None:
    captured: dict[str, Sequence[str]] = {}

    def spawn(cmd: Sequence[str], _env: MutableMapping[str, str]) -> int:
        captured["cmd"] = cmd
        return 0

    run_chat(
        store_dir=tmp_path,
        extra_args=("--model", "opus"),
        env={},
        spawn=spawn,
        which=_ok_which,
    )
    assert list(captured["cmd"]) == ["claude", "--model", "opus"]


@pytest.mark.parametrize("code", [0, 2, 130])
def test_returns_spawn_exit_code(tmp_path: Path, code: int) -> None:
    def spawn(_cmd: Sequence[str], _env: MutableMapping[str, str]) -> int:
        return code

    assert (
        run_chat(
            store_dir=tmp_path,
            extra_args=(),
            env={},
            spawn=spawn,
            which=_ok_which,
        )
        == code
    )


def test_missing_claude_binary_returns_127(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def spawn(_cmd: Sequence[str], _env: MutableMapping[str, str]) -> int:
        raise AssertionError("spawn should not be called when claude is missing")

    rc = run_chat(
        store_dir=tmp_path,
        extra_args=(),
        env={},
        spawn=spawn,
        which=_missing_which,
    )
    assert rc == 127
    captured = capsys.readouterr()
    assert "claude" in captured.err
    assert "PATH" in captured.err


def test_env_is_mutated_in_place(tmp_path: Path) -> None:
    def spawn(_cmd: Sequence[str], _env: MutableMapping[str, str]) -> int:
        return 0

    env: dict[str, str] = {"PRE_EXISTING": "value"}
    run_chat(
        store_dir=tmp_path,
        extra_args=(),
        env=env,
        spawn=spawn,
        which=_ok_which,
    )
    assert env["CADENCE_MEMORY_DIR"] == str(tmp_path.resolve())
    assert env["PRE_EXISTING"] == "value"
