"""Tests for the default project exclude globs."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from cadence_memory.config import load_config
from cadence_memory.defaults.exclude import DEFAULT_PROJECT_EXCLUDE


def test_default_project_exclude_is_tuple() -> None:
    assert isinstance(DEFAULT_PROJECT_EXCLUDE, tuple)


def test_default_project_exclude_minimum_size() -> None:
    assert len(DEFAULT_PROJECT_EXCLUDE) >= 18


def test_default_project_exclude_entries_are_non_empty_strings() -> None:
    for entry in DEFAULT_PROJECT_EXCLUDE:
        assert isinstance(entry, str)
        assert entry != ""


def test_default_project_exclude_has_no_duplicates() -> None:
    assert len(DEFAULT_PROJECT_EXCLUDE) == len(set(DEFAULT_PROJECT_EXCLUDE))


def test_default_project_exclude_contains_key_entries() -> None:
    expected = {
        "**/.venv/**",
        "**/__pycache__/**",
        "**/.pytest_cache/**",
        "**/.mypy_cache/**",
        "**/.tox/**",
        "**/dist/**",
        "**/node_modules/**",
        "**/.git/**",
        "**/*.egg-info/**",
    }
    assert expected.issubset(set(DEFAULT_PROJECT_EXCLUDE))


def _read_template_text() -> str:
    return (
        resources.files("cadence_memory.defaults")
        .joinpath("config.yaml")
        .read_text(encoding="utf-8")
    )


_LIST_ITEM_RE = re.compile(r'^\s*#?\s*-\s+"([^"]+)"\s*$', re.MULTILINE)


def _template_exclude_globs() -> tuple[str, ...]:
    """Extract every ``- "<glob>"`` list-item line from the template.

    The commented `exclude:` example is the only place in the template that
    uses the bare ``- "..."`` form, so this is what locks the YAML and the
    Python tuple together.
    """
    return tuple(_LIST_ITEM_RE.findall(_read_template_text()))


def test_config_template_lists_every_default_exclude_glob_in_canonical_form() -> None:
    template = _read_template_text()
    for glob in DEFAULT_PROJECT_EXCLUDE:
        canonical = f'- "{glob}"'
        assert canonical in template, f"missing {canonical!r} in config.yaml template"


def test_config_template_exclude_block_matches_default_exclude_exactly() -> None:
    assert _template_exclude_globs() == DEFAULT_PROJECT_EXCLUDE


def test_config_template_parses_through_load_config(tmp_path: Path) -> None:
    template = _read_template_text()
    target = tmp_path / "config.yaml"
    target.write_text(template, encoding="utf-8")

    cfg = load_config(target)

    assert cfg.projects == ()
