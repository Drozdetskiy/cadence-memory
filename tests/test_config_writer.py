"""Tests for ``ConfigWriter`` round-trip YAML editor."""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml.error import YAMLError

from cadence_memory.config import ConfigError, load_config
from cadence_memory.config_writer import ConfigWriter, ProjectAddResult
from cadence_memory.defaults.exclude import DEFAULT_PROJECT_EXCLUDE


def _write_config(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "config.yaml"
    target.write_text(body, encoding="utf-8")
    return target


def test_add_project_happy_path(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "projects: []\n")

    writer = ConfigWriter(config_path)
    result = writer.add_project(name="demo", path=Path("/tmp/demo"), exclude=("foo/**",))

    assert result == ProjectAddResult(name="demo", path=Path("/tmp/demo"), added=True)
    cfg = load_config(config_path)
    assert len(cfg.projects) == 1
    assert cfg.projects[0].name == "demo"
    assert cfg.projects[0].exclude == ("foo/**",)


def test_add_project_preserves_leading_comment(tmp_path: Path) -> None:
    body = "# top-of-file note\n# more lines about projects\nprojects: []\ndefaults:\n  kind: doc\n"
    config_path = _write_config(tmp_path, body)

    writer = ConfigWriter(config_path)
    writer.add_project(name="demo", path=Path("/tmp/demo"), exclude=())

    raw = config_path.read_text(encoding="utf-8")
    assert "# top-of-file note" in raw
    assert "# more lines about projects" in raw


def test_add_project_duplicate_returns_added_false_and_does_not_rewrite(
    tmp_path: Path,
) -> None:
    body = "projects:\n  - name: demo\n    path: /tmp/demo\n    exclude: []\n"
    config_path = _write_config(tmp_path, body)
    before = config_path.read_text(encoding="utf-8")
    before_mtime = config_path.stat().st_mtime_ns

    writer = ConfigWriter(config_path)
    result = writer.add_project(name="demo", path=Path("/tmp/other"))

    assert result.added is False
    assert config_path.read_text(encoding="utf-8") == before
    assert config_path.stat().st_mtime_ns == before_mtime


def test_remove_project_happy_path_preserves_sibling(tmp_path: Path) -> None:
    body = (
        "projects:\n"
        "  - name: keep\n"
        "    path: /tmp/keep\n"
        "    exclude: []\n"
        "  - name: drop\n"
        "    path: /tmp/drop\n"
        "    exclude: []\n"
    )
    config_path = _write_config(tmp_path, body)

    writer = ConfigWriter(config_path)
    removed = writer.remove_project("drop")

    assert removed is True
    cfg = load_config(config_path)
    assert [p.name for p in cfg.projects] == ["keep"]


def test_remove_project_missing_returns_false_and_does_not_rewrite(
    tmp_path: Path,
) -> None:
    body = "projects:\n  - name: keep\n    path: /tmp/keep\n    exclude: []\n"
    config_path = _write_config(tmp_path, body)
    before = config_path.read_text(encoding="utf-8")
    before_mtime = config_path.stat().st_mtime_ns

    writer = ConfigWriter(config_path)
    removed = writer.remove_project("nope")

    assert removed is False
    assert config_path.read_text(encoding="utf-8") == before
    assert config_path.stat().st_mtime_ns == before_mtime


def test_add_project_default_exclude_writes_full_tuple(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "projects: []\n")

    writer = ConfigWriter(config_path)
    writer.add_project(name="demo", path=Path("/tmp/demo"))

    cfg = load_config(config_path)
    assert cfg.projects[0].exclude == DEFAULT_PROJECT_EXCLUDE


def test_add_project_explicit_exclude_writes_verbatim(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "projects: []\n")

    writer = ConfigWriter(config_path)
    writer.add_project(name="demo", path=Path("/tmp/demo"), exclude=["custom/**", "other/**"])

    cfg = load_config(config_path)
    assert cfg.projects[0].exclude == ("custom/**", "other/**")


def test_add_project_empty_exclude_writes_empty_list(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "projects: []\n")

    writer = ConfigWriter(config_path)
    writer.add_project(name="demo", path=Path("/tmp/demo"), exclude=())

    cfg = load_config(config_path)
    assert cfg.projects[0].exclude == ()
    raw = config_path.read_text(encoding="utf-8")
    assert "exclude: []" in raw


def test_list_projects_matches_load_config(tmp_path: Path) -> None:
    body = (
        "projects:\n"
        "  - name: alpha\n"
        "    path: /tmp/alpha\n"
        "    exclude: []\n"
        "  - name: beta\n"
        "    path: /tmp/beta\n"
        "    exclude:\n"
        "      - foo/**\n"
    )
    config_path = _write_config(tmp_path, body)

    writer = ConfigWriter(config_path)
    listed = writer.list_projects()

    expected = list(load_config(config_path).projects)
    assert listed == expected
    assert [p.name for p in listed] == ["alpha", "beta"]


def test_add_project_when_projects_key_missing(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "defaults:\n  kind: doc\n")

    writer = ConfigWriter(config_path)
    writer.add_project(name="demo", path=Path("/tmp/demo"), exclude=())

    cfg = load_config(config_path)
    assert [p.name for p in cfg.projects] == ["demo"]


def test_add_project_when_projects_key_null(tmp_path: Path) -> None:
    body = "projects:\ndefaults:\n  kind: doc\n"
    config_path = _write_config(tmp_path, body)

    writer = ConfigWriter(config_path)
    writer.add_project(name="demo", path=Path("/tmp/demo"), exclude=())

    cfg = load_config(config_path)
    assert [p.name for p in cfg.projects] == ["demo"]


def test_add_project_can_be_followed_by_another_add(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "projects: []\n")

    writer = ConfigWriter(config_path)
    writer.add_project(name="alpha", path=Path("/tmp/alpha"), exclude=())

    writer2 = ConfigWriter(config_path)
    writer2.add_project(name="beta", path=Path("/tmp/beta"), exclude=())

    cfg = load_config(config_path)
    assert [p.name for p in cfg.projects] == ["alpha", "beta"]


def test_add_project_with_invalid_yaml_raises(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "this is: : not: yaml\n")

    with pytest.raises(YAMLError):
        ConfigWriter(config_path)


def test_constructor_refuses_non_list_projects(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "projects: not-a-list\n")

    with pytest.raises(ConfigError, match="projects must be a list"):
        ConfigWriter(config_path)

    assert config_path.read_text(encoding="utf-8") == "projects: not-a-list\n"


def test_add_project_preserves_existing_entries_and_comments(tmp_path: Path) -> None:
    body = (
        "projects:\n"
        "  - name: keep  # main repo\n"
        "    path: /tmp/keep\n"
        "    exclude:\n"
        "      - foo/**\n"
        "      - bar/**\n"
    )
    config_path = _write_config(tmp_path, body)

    writer = ConfigWriter(config_path)
    writer.add_project(name="new", path=Path("/tmp/new"), exclude=())

    cfg = load_config(config_path)
    assert [p.name for p in cfg.projects] == ["keep", "new"]
    assert cfg.projects[0].path == Path("/tmp/keep")
    assert cfg.projects[0].exclude == ("foo/**", "bar/**")
    raw = config_path.read_text(encoding="utf-8")
    assert "# main repo" in raw
