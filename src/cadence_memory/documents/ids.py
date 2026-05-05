"""Build, parse, and validate document IDs (<project>:<path> | :<path> | eph:<id>)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceType = Literal["project", "global", "ephemeral"]


@dataclass(frozen=True, slots=True)
class DocumentId:
    source_type: SourceType
    project: str | None
    rel_path: str | None


def _validate_rel_path(rel_path: str) -> None:
    if not rel_path:
        raise ValueError("invalid document path '': must be non-empty")
    if rel_path.startswith("/"):
        raise ValueError(f"invalid document path {rel_path!r}: must not start with '/'")
    if "\\" in rel_path:
        raise ValueError(f"invalid document path {rel_path!r}: must not contain backslash")
    if ":" in rel_path:
        raise ValueError(f"invalid document path {rel_path!r}: must not contain ':'")
    for segment in rel_path.split("/"):
        if segment == "..":
            raise ValueError(
                f"invalid document path {rel_path!r}: must not contain '..' path segment"
            )


def _validate_project_name(name: str) -> None:
    if not name:
        raise ValueError("invalid project name '': must be non-empty")
    if ":" in name:
        raise ValueError(f"invalid project name {name!r}: must not contain ':'")
    if "/" in name:
        raise ValueError(f"invalid project name {name!r}: must not contain '/'")
    if name == "eph":
        raise ValueError(f"invalid project name {name!r}: reserved (collides with 'eph:' prefix)")


def _validate_ephemeral_name(name: str) -> None:
    if not name:
        raise ValueError("invalid ephemeral name '': must be non-empty")
    if ":" in name:
        raise ValueError(f"invalid ephemeral name {name!r}: must not contain ':'")
    if "/" in name:
        raise ValueError(f"invalid ephemeral name {name!r}: must not contain '/'")
    if "\\" in name:
        raise ValueError(f"invalid ephemeral name {name!r}: must not contain backslash")


def build_project_id(project: str, rel_path: str) -> str:
    _validate_project_name(project)
    _validate_rel_path(rel_path)
    return f"{project}:{rel_path}"


def build_global_id(rel_path: str) -> str:
    _validate_rel_path(rel_path)
    return f":{rel_path}"


def build_ephemeral_id(name: str) -> str:
    _validate_ephemeral_name(name)
    return f"eph:{name}"


def parse(id_str: str) -> DocumentId:
    if id_str.startswith("eph:"):
        name = id_str[len("eph:") :]
        _validate_ephemeral_name(name)
        return DocumentId(source_type="ephemeral", project=None, rel_path=None)
    if id_str.startswith(":"):
        rel_path = id_str[1:]
        _validate_rel_path(rel_path)
        return DocumentId(source_type="global", project=None, rel_path=rel_path)
    if ":" not in id_str:
        raise ValueError(
            f"invalid document id {id_str!r}: missing ':' separator between project and path"
        )
    project, rel_path = id_str.split(":", 1)
    _validate_project_name(project)
    _validate_rel_path(rel_path)
    return DocumentId(source_type="project", project=project, rel_path=rel_path)


def validate(id_str: str) -> None:
    parse(id_str)
