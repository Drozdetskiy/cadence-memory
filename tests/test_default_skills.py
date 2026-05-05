"""Smoke tests for the bundled Claude Code skill markdown files."""

from __future__ import annotations

import importlib.resources


def test_query_skill_landmarks() -> None:
    root = importlib.resources.files("cadence_memory.defaults.skills")
    text = root.joinpath("cadence-memory.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: cadence-memory" in text
    assert "cadence-memory list --format json" in text
    assert "cadence-memory query" in text
