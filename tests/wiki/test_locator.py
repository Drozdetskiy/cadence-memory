"""Tests for the wiki locator's precedence chain and edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from cadence_memory.wiki import WikiNotFoundError, resolve_wiki_dir


def _make_wiki(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text("")
    return directory


def test_flag_wins(tmp_path_factory: pytest.TempPathFactory) -> None:
    flag_wiki = _make_wiki(tmp_path_factory.mktemp("flag") / "wiki")
    env_wiki = _make_wiki(tmp_path_factory.mktemp("env") / "wiki")
    walkup_root = _make_wiki(tmp_path_factory.mktemp("walkup"))
    nested_cwd = walkup_root / "a" / "b"
    nested_cwd.mkdir(parents=True)

    result = resolve_wiki_dir(
        flag=flag_wiki,
        env={"CADENCE_MEMORY_WIKI": str(env_wiki)},
        cwd=nested_cwd,
    )

    assert result == flag_wiki.resolve()


def test_flag_without_config_errors(tmp_path_factory: pytest.TempPathFactory) -> None:
    bad_flag = tmp_path_factory.mktemp("bad_flag")
    env_wiki = _make_wiki(tmp_path_factory.mktemp("env") / "wiki")
    walkup_root = _make_wiki(tmp_path_factory.mktemp("walkup"))

    with pytest.raises(WikiNotFoundError):
        resolve_wiki_dir(
            flag=bad_flag,
            env={"CADENCE_MEMORY_WIKI": str(env_wiki)},
            cwd=walkup_root,
        )


def test_flag_nonexistent_path_errors(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    with pytest.raises(WikiNotFoundError):
        resolve_wiki_dir(flag=missing, env={}, cwd=tmp_path)


def test_env_wins_over_walkup(tmp_path_factory: pytest.TempPathFactory) -> None:
    env_wiki = _make_wiki(tmp_path_factory.mktemp("env") / "wiki")
    walkup_root = _make_wiki(tmp_path_factory.mktemp("walkup"))
    nested_cwd = walkup_root / "a" / "b"
    nested_cwd.mkdir(parents=True)

    result = resolve_wiki_dir(
        env={"CADENCE_MEMORY_WIKI": str(env_wiki)},
        cwd=nested_cwd,
    )

    assert result == env_wiki.resolve()


def test_env_without_config_errors(tmp_path_factory: pytest.TempPathFactory) -> None:
    bad_env_dir = tmp_path_factory.mktemp("bad_env")
    walkup_root = _make_wiki(tmp_path_factory.mktemp("walkup"))

    with pytest.raises(WikiNotFoundError):
        resolve_wiki_dir(
            env={"CADENCE_MEMORY_WIKI": str(bad_env_dir)},
            cwd=walkup_root,
        )


def test_env_empty_string_falls_through_to_walkup(tmp_path: Path) -> None:
    _make_wiki(tmp_path)
    nested = tmp_path / "deep" / "nest"
    nested.mkdir(parents=True)

    result = resolve_wiki_dir(env={"CADENCE_MEMORY_WIKI": ""}, cwd=nested)

    assert result == tmp_path.resolve()


def test_env_missing_falls_through_to_walkup(tmp_path: Path) -> None:
    _make_wiki(tmp_path)
    nested = tmp_path / "deep" / "nest"
    nested.mkdir(parents=True)

    result = resolve_wiki_dir(env={}, cwd=nested)

    assert result == tmp_path.resolve()


def test_walkup_finds_nearest(tmp_path: Path) -> None:
    outer = tmp_path
    inner = tmp_path / "child"
    inner.mkdir()
    _make_wiki(outer)
    _make_wiki(inner)

    deeper = inner / "grand" / "child"
    deeper.mkdir(parents=True)

    result = resolve_wiki_dir(env={}, cwd=deeper)

    assert result == inner.resolve()


def test_walkup_none_found(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    with pytest.raises(WikiNotFoundError):
        resolve_wiki_dir(env={}, cwd=nested)


def test_symlinks_resolved(tmp_path_factory: pytest.TempPathFactory) -> None:
    real_dir = _make_wiki(tmp_path_factory.mktemp("real") / "wiki")
    link_root = tmp_path_factory.mktemp("link")
    symlink = link_root / "wiki-link"
    symlink.symlink_to(real_dir, target_is_directory=True)

    result = resolve_wiki_dir(flag=symlink, env={}, cwd=link_root)

    assert result == real_dir.resolve()
    assert result != symlink


def test_flag_is_file_errors(tmp_path: Path) -> None:
    _make_wiki(tmp_path)
    config_file = tmp_path / "config.yaml"

    with pytest.raises(WikiNotFoundError):
        resolve_wiki_dir(flag=config_file, env={}, cwd=tmp_path)
