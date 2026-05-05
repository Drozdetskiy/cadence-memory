"""Unit tests for cadence_memory.store_locator."""

from __future__ import annotations

from pathlib import Path

import pytest

from cadence_memory.store_locator import StoreNotFoundError, resolve_store_dir


def _make_store(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text("projects: []\n", encoding="utf-8")
    return directory


def test_flag_wins_over_env_and_cwd(tmp_path: Path) -> None:
    flag_store = _make_store(tmp_path / "flag")
    env_store = _make_store(tmp_path / "env")
    cwd_store = _make_store(tmp_path / "cwd")

    resolved = resolve_store_dir(
        flag=flag_store,
        env={"CADENCE_MEMORY_DIR": str(env_store)},
        cwd=cwd_store,
    )
    assert resolved == flag_store.resolve()


def test_env_wins_over_walk_up(tmp_path: Path) -> None:
    env_store = _make_store(tmp_path / "env")
    cwd_store = _make_store(tmp_path / "cwd")

    resolved = resolve_store_dir(
        flag=None,
        env={"CADENCE_MEMORY_DIR": str(env_store)},
        cwd=cwd_store,
    )
    assert resolved == env_store.resolve()


def test_walk_up_finds_nearest_ancestor(tmp_path: Path) -> None:
    store = _make_store(tmp_path / "outer")
    nested = store / "a" / "b" / "c"
    nested.mkdir(parents=True)

    resolved = resolve_store_dir(flag=None, env={}, cwd=nested)
    assert resolved == store.resolve()


def test_walk_up_picks_closest_ancestor_when_multiple(tmp_path: Path) -> None:
    outer = _make_store(tmp_path / "outer")
    inner = _make_store(outer / "inner")
    nested = inner / "child"
    nested.mkdir()

    resolved = resolve_store_dir(flag=None, env={}, cwd=nested)
    assert resolved == inner.resolve()


def test_empty_env_falls_through_to_walk_up(tmp_path: Path) -> None:
    store = _make_store(tmp_path / "outer")
    nested = store / "deep"
    nested.mkdir()

    resolved = resolve_store_dir(
        flag=None,
        env={"CADENCE_MEMORY_DIR": ""},
        cwd=nested,
    )
    assert resolved == store.resolve()


def test_flag_directory_without_config_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "no-store"
    bogus.mkdir()

    with pytest.raises(StoreNotFoundError) as exc_info:
        resolve_store_dir(flag=bogus, env={}, cwd=tmp_path)
    msg = str(exc_info.value)
    assert "--store" in msg
    assert str(bogus.resolve()) in msg


def test_env_directory_without_config_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "env-no-store"
    bogus.mkdir()

    with pytest.raises(StoreNotFoundError) as exc_info:
        resolve_store_dir(
            flag=None,
            env={"CADENCE_MEMORY_DIR": str(bogus)},
            cwd=tmp_path,
        )
    msg = str(exc_info.value)
    assert "CADENCE_MEMORY_DIR" in msg
    assert str(bogus.resolve()) in msg


def test_no_store_anywhere_raises(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)

    with pytest.raises(StoreNotFoundError) as exc_info:
        resolve_store_dir(flag=None, env={}, cwd=nested)
    msg = str(exc_info.value)
    assert "--store" in msg
    assert "CADENCE_MEMORY_DIR" in msg


def test_filesystem_root_with_no_store_raises() -> None:
    root = Path("/")
    with pytest.raises(StoreNotFoundError):
        resolve_store_dir(flag=None, env={}, cwd=root)


def test_flag_expands_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    store = _make_store(home / "store")

    resolved = resolve_store_dir(flag=Path("~/store"), env={}, cwd=tmp_path)
    assert resolved == store.resolve()
