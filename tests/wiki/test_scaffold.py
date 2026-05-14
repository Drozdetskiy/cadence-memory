"""Tests for `scaffold_wiki` (design2 §4, §11)."""

from __future__ import annotations

import re
import stat
import subprocess
from datetime import date
from importlib.resources import files
from pathlib import Path

import pytest

from cadence_memory.documents.frontmatter import parse_page
from cadence_memory.wiki import ScaffoldResult, scaffold_wiki
from cadence_memory.wiki import init as init_module

_EXPECTED_FILES: tuple[str, ...] = (
    "config.yaml",
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/skills/wiki-researcher/SKILL.md",
    ".claude/skills/wiki-ingest/SKILL.md",
    ".gitignore",
    "index.md",
    "log.md",
    "gaps.md",
    "raw/.gitkeep",
    "projects/.gitkeep",
    ".git/hooks/post-commit",
)


def test_fresh_scaffold_creates_full_tree(tmp_path: Path) -> None:
    result = scaffold_wiki(tmp_path)

    assert isinstance(result, ScaffoldResult)
    assert result.git_initialized is True
    assert (tmp_path / ".git").is_dir()
    assert result.target == tmp_path.resolve()

    created_set = set(result.created_files)
    expected_set = {(tmp_path / rel).resolve() for rel in _EXPECTED_FILES}
    assert created_set == expected_set
    for created in result.created_files:
        assert created.is_absolute()
        assert created.is_file()
    assert result.skipped_files == ()

    hook = (tmp_path / ".git" / "hooks" / "post-commit").resolve()
    assert hook in created_set
    assert stat.S_IXUSR & hook.stat().st_mode


def test_idempotent_second_run_creates_nothing(tmp_path: Path) -> None:
    first = scaffold_wiki(tmp_path)
    snapshots = {p: p.read_bytes() for p in first.created_files}

    second = scaffold_wiki(tmp_path)

    assert second.created_files == ()
    assert set(second.skipped_files) == set(first.created_files)
    assert second.git_initialized is False
    for path, original in snapshots.items():
        assert path.read_bytes() == original


def test_existing_git_repo_not_reinitialized(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    head = tmp_path / ".git" / "HEAD"
    mtime_before = head.stat().st_mtime_ns

    result = scaffold_wiki(tmp_path)

    assert result.git_initialized is False
    assert head.stat().st_mtime_ns == mtime_before


def test_hand_edited_config_preserved(tmp_path: Path) -> None:
    custom = b"# my custom config\n"
    (tmp_path / "config.yaml").write_bytes(custom)

    result = scaffold_wiki(tmp_path)

    config_path = (tmp_path / "config.yaml").resolve()
    assert (tmp_path / "config.yaml").read_bytes() == custom
    assert config_path in result.skipped_files
    assert config_path not in result.created_files


def test_seed_dates_substituted_in_index(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)
    today = date.today().isoformat()

    text = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert f"created: {today}" in text
    assert f"updated: {today}" in text
    assert "{date}" not in text


def test_log_entry_has_today(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)
    today = date.today().isoformat()

    text = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert f"## [{today}] init | wiki scaffolded" in text
    assert "{date}" not in text


def test_seed_pages_pass_frontmatter_parser(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)

    index = parse_page(tmp_path / "index.md")
    log = parse_page(tmp_path / "log.md")
    gaps = parse_page(tmp_path / "gaps.md")

    assert index.frontmatter.type == "overview"
    assert log.frontmatter.type == "log"
    assert gaps.frontmatter.type == "gaps"


def test_target_directory_is_created_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "new" / "wiki"

    result = scaffold_wiki(nested)

    assert nested.is_dir()
    assert (nested / "config.yaml").is_file()
    assert (nested / "index.md").is_file()
    assert result.target == nested.resolve()


def test_scaffold_in_subdirectory_of_existing_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    nested = tmp_path / "wiki"

    result = scaffold_wiki(nested)

    assert result.git_initialized is False
    assert (nested / "config.yaml").is_file()
    hook_path = (nested / ".git" / "hooks" / "post-commit").resolve()
    assert not hook_path.exists()
    assert hook_path in set(result.skipped_files)


def test_scaffold_preserves_foreign_post_commit_hook(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign_script = b"#!/bin/bash\necho foreign\n"
    hook_file = hooks_dir / "post-commit"
    hook_file.write_bytes(foreign_script)

    result = scaffold_wiki(tmp_path)

    assert hook_file.read_bytes() == foreign_script
    hook_path = hook_file.resolve()
    assert hook_path in set(result.skipped_files)
    assert hook_path not in set(result.created_files)


_EXPECTED_GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".cadence-memory/git_cache/",
    ".cadence-memory/state.json",
    ".cadence-memory/worker.lock",
    "*.tmp",
    ".DS_Store",
    ".claude/settings.local.json",
    "raw/notes/",
)


def test_scaffolded_gitignore_contains_expected_entries(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)

    lines = {
        line.strip()
        for line in (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for entry in _EXPECTED_GITIGNORE_ENTRIES:
        assert entry in lines, f"missing gitignore entry: {entry}"


def test_scaffolded_gitignore_idempotent_content(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)
    first = (tmp_path / ".gitignore").read_bytes()

    scaffold_wiki(tmp_path)
    second = (tmp_path / ".gitignore").read_bytes()

    assert first == second


def _query_protocol_section(claudemd_text: str) -> str:
    lines = claudemd_text.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "## Query protocol":
            start = i + 1
            break
    assert start is not None, "## Query protocol heading not found"
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


def test_scaffolded_claudemd_query_protocol_has_five_steps(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    section = _query_protocol_section(text)
    items = re.findall(r"^\d+\. ", section, flags=re.MULTILINE)
    assert len(items) == 5, f"expected 5 numbered steps, got {len(items)}: {items}"


def test_scaffolded_claudemd_mentions_wiki_researcher(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    section = _query_protocol_section(text)
    assert "wiki-researcher" in section
    normalized = re.sub(r"\s+", " ", section).lower()
    assert "in parallel" in normalized


def test_scaffolded_index_raw_notes_phrasing_matches_gitignore_policy(
    tmp_path: Path,
) -> None:
    scaffold_wiki(tmp_path)
    text = (tmp_path / "index.md").read_text(encoding="utf-8")

    lines = text.splitlines()
    bullet_idx: int | None = None
    for i, line in enumerate(lines):
        if line.startswith("- `raw/notes/`"):
            bullet_idx = i
            break
    assert bullet_idx is not None, "raw/notes/ bullet not found in index.md"

    end = len(lines)
    for j in range(bullet_idx + 1, len(lines)):
        stripped = lines[j].lstrip()
        if not lines[j].strip():
            end = j
            break
        if stripped.startswith("- ") or stripped.startswith("#"):
            end = j
            break
    bullet_block = "\n".join(lines[bullet_idx:end])

    assert "paste manually-curated notes" not in bullet_block
    assert "Not committed" in bullet_block


def test_wiki_config_template_has_no_budget_usd() -> None:
    template = files("cadence_memory.defaults") / "wiki" / "config.yaml"
    content = template.read_text(encoding="utf-8")
    assert "budget_usd" not in content


def test_atomic_write_failure_cleans_up_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_replace(src: str | Path, dst: str | Path) -> None:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(init_module.os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated atomic replace failure"):
        scaffold_wiki(tmp_path)

    assert list(tmp_path.rglob("*.tmp")) == []
