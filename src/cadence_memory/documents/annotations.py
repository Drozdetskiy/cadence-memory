"""Merge annotations-config with frontmatter (frontmatter wins for kind/title; sets union tags)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MergedAnnotation:
    kind: str
    title: str
    tags: tuple[str, ...]
    related: tuple[str, ...]
    project: str | None
    confidence: str | None
    last_confirmed_at: str | None
    provenance: Mapping[str, str]


def _is_absent(value: object) -> bool:
    return value is None or value == ""


def _pick_scalar(
    field_name: str,
    frontmatter: Mapping[str, object],
    annotations_entry: Mapping[str, object] | None,
) -> tuple[str, str] | None:
    fm_value = frontmatter.get(field_name)
    if not _is_absent(fm_value):
        return str(fm_value), "frontmatter"
    if annotations_entry is not None:
        ann_value = annotations_entry.get(field_name)
        if not _is_absent(ann_value):
            return str(ann_value), "annotations-config"
    return None


def _coerce_list(
    field_name: str,
    source_label: str,
    raw: object,
) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"{source_label} field {field_name!r} must be a list of strings, "
            f"got {type(raw).__name__}"
        )
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(
                f"{source_label} field {field_name!r} must contain only strings, "
                f"got element of type {type(item).__name__}"
            )
        out.append(item)
    return out


def _merge_list(
    field_name: str,
    frontmatter: Mapping[str, object],
    annotations_entry: Mapping[str, object] | None,
) -> tuple[tuple[str, ...], str]:
    ann_items = _coerce_list(
        field_name,
        "annotations-config",
        annotations_entry.get(field_name) if annotations_entry is not None else None,
    )
    fm_items = _coerce_list(field_name, "frontmatter", frontmatter.get(field_name))

    seen: set[str] = set()
    merged: list[str] = []
    for item in (*ann_items, *fm_items):
        if item not in seen:
            seen.add(item)
            merged.append(item)

    if ann_items and fm_items:
        provenance = "merged"
    elif fm_items:
        provenance = "frontmatter"
    elif ann_items:
        provenance = "annotations-config"
    else:
        provenance = "default"
    return tuple(merged), provenance


def merge(
    *,
    annotations_entry: Mapping[str, object] | None,
    frontmatter: Mapping[str, object],
    h1_title: str | None,
    filename_fallback: str,
    defaults_kind: str,
) -> MergedAnnotation:
    provenance: dict[str, str] = {}

    kind_pair = _pick_scalar("kind", frontmatter, annotations_entry)
    if kind_pair is None:
        kind = defaults_kind
        provenance["kind"] = "default"
    else:
        kind, provenance["kind"] = kind_pair

    title_pair = _pick_scalar("title", frontmatter, annotations_entry)
    if title_pair is not None:
        title, provenance["title"] = title_pair
    elif h1_title is not None and h1_title != "":
        title = h1_title
        provenance["title"] = "default"
    else:
        title = filename_fallback
        provenance["title"] = "default"

    tags, provenance["tags"] = _merge_list("tags", frontmatter, annotations_entry)
    related, provenance["related"] = _merge_list("related", frontmatter, annotations_entry)

    project_pair = _pick_scalar("project", frontmatter, annotations_entry)
    project = project_pair[0] if project_pair is not None else None
    provenance["project"] = project_pair[1] if project_pair is not None else "default"

    confidence_pair = _pick_scalar("confidence", frontmatter, annotations_entry)
    confidence = confidence_pair[0] if confidence_pair is not None else None
    provenance["confidence"] = confidence_pair[1] if confidence_pair is not None else "default"

    last_confirmed_pair = _pick_scalar("last_confirmed_at", frontmatter, annotations_entry)
    last_confirmed_at = last_confirmed_pair[0] if last_confirmed_pair is not None else None
    provenance["last_confirmed_at"] = (
        last_confirmed_pair[1] if last_confirmed_pair is not None else "default"
    )

    return MergedAnnotation(
        kind=kind,
        title=title,
        tags=tags,
        related=related,
        project=project,
        confidence=confidence,
        last_confirmed_at=last_confirmed_at,
        provenance=dict(provenance),
    )
