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


def test_discover_skill_landmarks() -> None:
    root = importlib.resources.files("cadence_memory.defaults.skills")
    text = root.joinpath("cadence-memory-discover.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: cadence-memory-discover" in text
    assert "documents:" in text
    assert "kind_rules" in text
    assert "## What NOT to do" in text


def test_discover_prompt_landmarks() -> None:
    root = importlib.resources.files("cadence_memory.defaults.prompts")
    text = root.joinpath("discover.txt").read_text(encoding="utf-8")
    assert "$store_dir" in text
    assert "$output_path" in text
    assert "$projects_block" in text
    assert "do not print it back" in text
