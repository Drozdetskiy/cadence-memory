"""End-to-end tests for the `cadence-memory chat` CLI command."""

from __future__ import annotations

import subprocess
from collections.abc import MutableMapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory import cli
from cadence_memory.cli import app

runner = CliRunner()


def _make_store(tmp_path: Path) -> Path:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "config.yaml").write_text("projects: []\n", encoding="utf-8")
    return store_dir


def _install_fake_run_chat(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_code: int = 0,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run_chat(
        *,
        store_dir: Path,
        extra_args: Sequence[str],
        env: MutableMapping[str, str],
    ) -> int:
        captured["store_dir"] = store_dir
        captured["extra_args"] = tuple(extra_args)
        captured["env"] = dict(env)
        return return_code

    monkeypatch.setattr(cli, "_chat_run", fake_run_chat)
    return captured


def _stub_home_without_skill(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))


def _stub_home_with_skill(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    skill = fake_home / ".claude" / "skills" / "cadence-memory" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\nname: cadence-memory\n---\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))


def test_chat_resolves_store_via_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)
    _install_fake_run_chat(monkeypatch)

    result = runner.invoke(app, ["--store", str(store_dir), "chat"])

    assert result.exit_code == 0, result.output
    assert f"chat: store={store_dir}" in result.output


def test_chat_resolves_store_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)
    _install_fake_run_chat(monkeypatch)

    result = runner.invoke(
        app,
        ["chat"],
        env={"CADENCE_MEMORY_DIR": str(store_dir)},
    )

    assert result.exit_code == 0, result.output
    assert f"chat: store={store_dir}" in result.output


@pytest.mark.parametrize("rc", [0, 2])
def test_chat_exits_with_run_chat_return_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rc: int
) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)
    _install_fake_run_chat(monkeypatch, return_code=rc)

    result = runner.invoke(app, ["--store", str(store_dir), "chat"])

    assert result.exit_code == rc, result.output


def test_chat_forwards_trailing_args_after_double_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)
    captured = _install_fake_run_chat(monkeypatch)

    result = runner.invoke(app, ["--store", str(store_dir), "chat", "--", "--model", "opus"])

    assert result.exit_code == 0, result.output
    assert captured["extra_args"] == ("--model", "opus")


def test_chat_with_no_trailing_args_passes_empty_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)
    captured = _install_fake_run_chat(monkeypatch)

    result = runner.invoke(app, ["--store", str(store_dir), "chat"])

    assert result.exit_code == 0, result.output
    assert captured["extra_args"] == ()


def test_chat_injects_cadence_memory_dir_into_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)

    captured: dict[str, Any] = {}

    def fake_spawn(cmd: Sequence[str], env: MutableMapping[str, str]) -> int:
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)
        return 0

    monkeypatch.setattr(cli, "_chat_spawn", fake_spawn)
    monkeypatch.setattr(cli, "_chat_which", lambda _name: "/usr/local/bin/claude")

    result = runner.invoke(app, ["--store", str(store_dir), "chat"])

    assert result.exit_code == 0, result.output
    assert captured["cmd"] == ["claude"]
    assert captured["env"]["CADENCE_MEMORY_DIR"] == str(store_dir.resolve())


def test_chat_exits_127_when_claude_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)

    def must_not_spawn(_cmd: Sequence[str], _env: MutableMapping[str, str]) -> int:
        raise AssertionError("spawn must not run when claude is missing")

    monkeypatch.setattr(cli, "_chat_spawn", must_not_spawn)
    monkeypatch.setattr(cli, "_chat_which", lambda _name: None)

    result = runner.invoke(app, ["--store", str(store_dir), "chat"])

    assert result.exit_code == 127


def test_chat_prints_install_hint_when_skill_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_without_skill(monkeypatch, fake_home)
    _install_fake_run_chat(monkeypatch)

    result = runner.invoke(app, ["--store", str(store_dir), "chat"])

    assert result.exit_code == 0, result.output
    assert "note: install the skill once with:" in result.output
    assert "mkdir -p ~/.claude/skills/cadence-memory" in result.output
    assert "~/.claude/skills/cadence-memory/SKILL.md" in result.output


def test_chat_install_hint_command_resolves_to_real_skill_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_without_skill(monkeypatch, fake_home)
    _install_fake_run_chat(monkeypatch)

    result = runner.invoke(app, ["--store", str(store_dir), "chat"])
    assert result.exit_code == 0, result.output

    completed = subprocess.run(
        ["bash", "-c", f"echo {_extract_cp_source(result.output)}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    skill_path = Path(completed.stdout.strip())
    assert skill_path.exists(), (
        f"install hint's command must resolve to a real file; got {skill_path}"
    )


def _extract_cp_source(hint_output: str) -> str:
    for line in hint_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("cp $("):
            return stripped[len("cp ") : stripped.rindex(")") + 1]
    raise AssertionError(f"no `cp $(...)` line in hint output:\n{hint_output}")


def test_chat_suppresses_install_hint_when_skill_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = _make_store(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)
    _install_fake_run_chat(monkeypatch)

    result = runner.invoke(app, ["--store", str(store_dir), "chat"])

    assert result.exit_code == 0, result.output
    assert "note: install the skill once with:" not in result.output


def test_chat_errors_when_store_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _stub_home_with_skill(monkeypatch, fake_home)

    def must_not_run(**_kwargs: Any) -> int:
        raise AssertionError("_chat_run must not be invoked when store is missing")

    monkeypatch.setattr(cli, "_chat_run", must_not_run)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["chat"], env={"CADENCE_MEMORY_DIR": ""})

    assert result.exit_code == 1
    assert "error:" in result.output
