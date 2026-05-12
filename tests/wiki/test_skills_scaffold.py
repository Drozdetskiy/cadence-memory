"""Tests for skill-file scaffolding (design2 §12)."""

from __future__ import annotations

from pathlib import Path

import yaml

from cadence_memory.wiki import scaffold_wiki

_SKILL_PATHS: tuple[str, ...] = (
    ".claude/skills/wiki-researcher.md",
    ".claude/skills/wiki-ingest.md",
)


def test_init_copies_skills(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)

    for rel in _SKILL_PATHS:
        assert (tmp_path / rel).is_file(), f"missing {rel}"


def test_skill_frontmatter_has_required_fields(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)

    for rel in _SKILL_PATHS:
        path = tmp_path / rel
        text = path.read_text(encoding="utf-8")
        # Extract YAML between first two --- delimiters
        parts = text.split("---", 2)
        assert len(parts) >= 3, f"{rel}: no frontmatter delimiters found"
        fm = yaml.safe_load(parts[1])

        assert isinstance(fm.get("name"), str) and fm["name"], f"{rel}: missing name"
        assert isinstance(fm.get("description"), str) and fm["description"], (
            f"{rel}: missing description"
        )
        assert fm["name"] == path.stem, f"{rel}: name '{fm['name']}' != stem '{path.stem}'"


def test_idempotent_skills_copy(tmp_path: Path) -> None:
    custom = b"# hand-edited skill\n"
    skill_path = tmp_path / ".claude" / "skills" / "wiki-researcher.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_bytes(custom)

    result = scaffold_wiki(tmp_path)

    assert skill_path.read_bytes() == custom
    assert skill_path.resolve() in result.skipped_files


def test_claude_md_contains_trigger_line(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)

    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    trigger = (
        "Always check `index.md` and the `projects/` tree"
        " before answering questions about this knowledge base's contents."
    )
    assert trigger in text
    assert "## Maintenance" in text
    assert "## Operations" in text
