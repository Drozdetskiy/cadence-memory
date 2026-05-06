"""Smoke imports for runtime dependencies declared in pyproject.toml."""

from __future__ import annotations

import importlib


def test_ruamel_yaml_importable() -> None:
    module = importlib.import_module("ruamel.yaml")
    assert module is not None
    assert hasattr(module, "YAML")
