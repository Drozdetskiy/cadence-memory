"""Unit tests for the discover scanner."""

from __future__ import annotations

import os
from pathlib import Path

from cadence_memory.config import (
    DiscoverConfig,
    GlobalsConfig,
    ProjectConfig,
)
from cadence_memory.discover.scanner import (
    DiscoveredFile,
    scan_globals,
    scan_project,
)


def _make_project(
    path: Path,
    *,
    name: str = "alpha",
    exclude: tuple[str, ...] = (),
) -> ProjectConfig:
    return ProjectConfig(
        name=name,
        path=path,
        exclude=exclude,
        discover=DiscoverConfig(kind_rules=()),
    )


def _write(path: Path, content: str = "stub\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_project_returns_all_md_files_sorted_by_rel_path(tmp_path: Path) -> None:
    _write(tmp_path / "root.md")
    _write(tmp_path / "docs" / "guide.md")
    _write(tmp_path / "docs" / "deep" / "deep.md")

    files = scan_project(_make_project(tmp_path))

    assert [item.rel_path for item in files] == [
        "docs/deep/deep.md",
        "docs/guide.md",
        "root.md",
    ]
    assert all(isinstance(item, DiscoveredFile) for item in files)
    assert all(item.project == "alpha" for item in files)
    assert files[0].abs_path == tmp_path / "docs" / "deep" / "deep.md"


def test_scan_project_drops_files_under_excluded_directory(tmp_path: Path) -> None:
    _write(tmp_path / "public.md")
    _write(tmp_path / "docs" / "private" / "secret.md")
    _write(tmp_path / "docs" / "private" / "deep" / "secret2.md")
    _write(tmp_path / "docs" / "open.md")

    files = scan_project(_make_project(tmp_path, exclude=("docs/private/**",)))

    assert [item.rel_path for item in files] == ["docs/open.md", "public.md"]


def test_scan_project_glob_starstar_matches_at_root_and_nested(tmp_path: Path) -> None:
    _write(tmp_path / "test_x.md")
    _write(tmp_path / "keep.md")
    _write(tmp_path / "a" / "b" / "test_x.md")
    _write(tmp_path / "a" / "b" / "keep.md")

    files = scan_project(_make_project(tmp_path, exclude=("**/test_*.md",)))

    assert [item.rel_path for item in files] == ["a/b/keep.md", "keep.md"]


def test_scan_project_ignores_non_md_files(tmp_path: Path) -> None:
    _write(tmp_path / "keep.md")
    _write(tmp_path / "notes.txt")
    _write(tmp_path / "code.py")

    files = scan_project(_make_project(tmp_path))

    assert [item.rel_path for item in files] == ["keep.md"]


def test_scan_project_includes_uppercase_md_suffix(tmp_path: Path) -> None:
    _write(tmp_path / "README.MD")
    _write(tmp_path / "lower.md")

    files = scan_project(_make_project(tmp_path))

    assert sorted(item.rel_path for item in files) == ["README.MD", "lower.md"]


def test_scan_project_drops_symlink_pointing_outside_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    sibling = tmp_path / "sibling"
    project_dir.mkdir()
    sibling.mkdir()

    outside_target = sibling / "outside.md"
    outside_target.write_text("outside\n", encoding="utf-8")
    _write(project_dir / "real.md")

    link = project_dir / "link.md"
    os.symlink(outside_target, link)

    files = scan_project(_make_project(project_dir))

    assert [item.rel_path for item in files] == ["real.md"]


def test_scan_project_drops_broken_symlink(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write(project_dir / "real.md")

    broken = project_dir / "broken.md"
    os.symlink(project_dir / "does-not-exist.md", broken)

    files = scan_project(_make_project(project_dir))

    assert [item.rel_path for item in files] == ["real.md"]


def test_scan_project_keeps_symlink_pointing_inside_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    target = project_dir / "real.md"
    target.write_text("body\n", encoding="utf-8")

    link = project_dir / "inside.md"
    os.symlink(target, link)

    files = scan_project(_make_project(project_dir))

    assert sorted(item.rel_path for item in files) == ["inside.md", "real.md"]


def test_scan_globals_uses_default_include_and_excludes_ephemeral_and_config(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "concepts" / "billing.md")
    _write(tmp_path / "patterns" / "outbox.md")
    _write(tmp_path / "ephemeral" / "foo.md")
    _write(tmp_path / "annotations-config.yaml", "documents: []\n")
    _write(tmp_path / "annotations-config.yaml.proposed", "documents: []\n")

    globals_cfg = GlobalsConfig(
        include=("**/*.md",),
        exclude=("ephemeral/**", "annotations-config.yaml*"),
    )

    files = scan_globals(tmp_path, globals_cfg)

    assert [item.rel_path for item in files] == [
        "concepts/billing.md",
        "patterns/outbox.md",
    ]


def test_scan_globals_results_have_project_none(tmp_path: Path) -> None:
    _write(tmp_path / "root.md")
    _write(tmp_path / "nested" / "child.md")

    globals_cfg = GlobalsConfig(include=("**/*.md",), exclude=())

    files = scan_globals(tmp_path, globals_cfg)

    assert files
    assert all(item.project is None for item in files)


def test_scan_project_is_deterministic_across_repeated_calls(tmp_path: Path) -> None:
    _write(tmp_path / "b.md")
    _write(tmp_path / "a.md")
    _write(tmp_path / "nested" / "z.md")

    project = _make_project(tmp_path)
    first = scan_project(project)
    second = scan_project(project)

    assert first == second
