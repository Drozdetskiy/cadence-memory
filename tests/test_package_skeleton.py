"""Skeleton checks for the cadence-memory package layout."""

from __future__ import annotations

import importlib
import importlib.resources

import yaml
from typer.testing import CliRunner

import cadence_memory
from cadence_memory.cli import app

_MODULES = [
    "cadence_memory",
    "cadence_memory.cli",
    "cadence_memory.config",
    "cadence_memory.ephemeral",
    "cadence_memory.store_locator",
    "cadence_memory.documents",
    "cadence_memory.documents.ids",
    "cadence_memory.documents.hashes",
    "cadence_memory.documents.parser",
    "cadence_memory.documents.annotations",
    "cadence_memory.store",
    "cadence_memory.store.schema",
    "cadence_memory.store.interface",
    "cadence_memory.store.sqlite_store",
    "cadence_memory.reindex",
    "cadence_memory.reindex.engine",
    "cadence_memory.reindex.diff",
    "cadence_memory.discover",
    "cadence_memory.discover.scanner",
    "cadence_memory.discover.runner",
    "cadence_memory.executor",
    "cadence_memory.executor.events",
    "cadence_memory.executor.process_group",
    "cadence_memory.executor.claude_executor",
    "cadence_memory.formatters",
    "cadence_memory.formatters.json_format",
    "cadence_memory.formatters.table_format",
    "cadence_memory.defaults",
    "cadence_memory.defaults.prompts",
    "cadence_memory.defaults.skills",
]

_RESOURCE_FILES = [
    "config.yaml",
    "annotations-config.yaml",
    "gitignore",
    "prompts/discover.txt",
    "skills/cadence-memory.md",
    "skills/cadence-memory-discover.md",
]


def test_every_module_imports() -> None:
    for name in _MODULES:
        assert importlib.import_module(name) is not None, name


def test_version_constant() -> None:
    assert cadence_memory.__version__ == "0.2.0"


def test_cli_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == "cadence-memory 0.2.0\n"


def test_default_resources_ship_with_package() -> None:
    root = importlib.resources.files("cadence_memory.defaults")
    for relpath in _RESOURCE_FILES:
        resource = root.joinpath(relpath)
        assert resource.is_file(), relpath


def test_default_yaml_templates_parse() -> None:
    root = importlib.resources.files("cadence_memory.defaults")
    config_text = root.joinpath("config.yaml").read_text(encoding="utf-8")
    annotations_text = root.joinpath("annotations-config.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(config_text) == {
        "projects": [],
        "defaults": {"kind": "doc"},
    }
    assert yaml.safe_load(annotations_text) == {"documents": []}


def test_python_frontmatter_dependency_importable() -> None:
    frontmatter = importlib.import_module("frontmatter")
    assert frontmatter is not None
