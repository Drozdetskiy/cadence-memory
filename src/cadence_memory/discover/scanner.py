"""Scan project and global trees for .md files honoring exclude globs from config."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from cadence_memory.config import GlobalsConfig, ProjectConfig

__all__ = ["DiscoveredFile", "scan_globals", "scan_project"]


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    project: str | None
    abs_path: Path
    rel_path: str


def _match(pattern: str, rel_posix: str) -> bool:
    if fnmatch.fnmatch(rel_posix, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(rel_posix, pattern[3:])


def _iter_md_files(root: Path) -> Iterator[Path]:
    resolved_root = root.resolve()
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            candidate = Path(dirpath) / name
            if candidate.suffix.lower() != ".md":
                continue
            if candidate.is_symlink():
                try:
                    target = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not target.is_relative_to(resolved_root):
                    continue
            yield candidate


def scan_project(project: ProjectConfig) -> list[DiscoveredFile]:
    root = project.path
    results: list[DiscoveredFile] = []
    for abs_path in _iter_md_files(root):
        rel_posix = abs_path.relative_to(root).as_posix()
        if any(_match(pattern, rel_posix) for pattern in project.exclude):
            continue
        results.append(DiscoveredFile(project=project.name, abs_path=abs_path, rel_path=rel_posix))
    results.sort(key=lambda item: item.rel_path)
    return results


def scan_globals(store_dir: Path, globals_cfg: GlobalsConfig) -> list[DiscoveredFile]:
    results: list[DiscoveredFile] = []
    for abs_path in _iter_md_files(store_dir):
        rel_posix = abs_path.relative_to(store_dir).as_posix()
        if not any(_match(pattern, rel_posix) for pattern in globals_cfg.include):
            continue
        if any(_match(pattern, rel_posix) for pattern in globals_cfg.exclude):
            continue
        results.append(DiscoveredFile(project=None, abs_path=abs_path, rel_path=rel_posix))
    results.sort(key=lambda item: item.rel_path)
    return results
