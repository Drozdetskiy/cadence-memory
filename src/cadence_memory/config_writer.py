"""Round-trip YAML editor for ``config.yaml``.

Edits the ``projects:`` section while preserving comments and surrounding
formatting via ``ruamel.yaml``. Writes back only when something changed so
file mtime stays stable for no-op operations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from cadence_memory.config import ConfigError, ProjectConfig, load_config
from cadence_memory.defaults.exclude import DEFAULT_PROJECT_EXCLUDE


@dataclass(frozen=True, slots=True)
class ProjectAddResult:
    name: str
    path: Path
    added: bool


class ConfigWriter:
    """Round-trip editor for ``config.yaml`` (preserves comments)."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = self._yaml.load(handle)
        self._tree: CommentedMap = loaded if isinstance(loaded, CommentedMap) else CommentedMap()
        existing = self._tree.get("projects")
        if existing is not None and not isinstance(existing, CommentedSeq):
            raise ConfigError(
                f"{config_path}: projects must be a list, got {type(existing).__name__}"
            )

    def add_project(
        self,
        *,
        name: str,
        path: Path,
        exclude: Sequence[str] | None = None,
    ) -> ProjectAddResult:
        """Append a new project entry. Returns ``added=False`` on duplicates."""
        projects = self._projects_seq()
        for entry in projects:
            if isinstance(entry, dict) and entry.get("name") == name:
                return ProjectAddResult(name=name, path=path, added=False)

        exclude_globs = DEFAULT_PROJECT_EXCLUDE if exclude is None else tuple(exclude)

        entry = CommentedMap()
        entry["name"] = name
        entry["path"] = str(path)
        entry["exclude"] = self._build_exclude_seq(exclude_globs)
        projects.append(entry)
        self._tree["projects"] = projects
        self._dump()
        return ProjectAddResult(name=name, path=path, added=True)

    def remove_project(self, name: str) -> bool:
        """Remove the entry with the given ``name``; return whether removed."""
        projects = self._projects_seq()
        for index, entry in enumerate(projects):
            if isinstance(entry, dict) and entry.get("name") == name:
                del projects[index]
                self._tree["projects"] = projects
                self._dump()
                return True
        return False

    def list_projects(self) -> list[ProjectConfig]:
        """Return the parsed projects via ``load_config`` (consistency with reindex)."""
        cfg = load_config(self._config_path)
        return list(cfg.projects)

    def _projects_seq(self) -> CommentedSeq:
        existing = self._tree.get("projects")
        if isinstance(existing, CommentedSeq):
            return existing
        return CommentedSeq()

    @staticmethod
    def _build_exclude_seq(globs: Sequence[str]) -> CommentedSeq:
        seq = CommentedSeq()
        for glob in globs:
            seq.append(glob)
        return seq

    def _dump(self) -> None:
        with self._config_path.open("w", encoding="utf-8") as handle:
            self._yaml.dump(self._tree, handle)
