"""Reindex engine: three-hash detection driving INSERT/UPDATE/DELETE with metadata short-circuit."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cadence_memory.config import AnnotationsConfig, Config, DocumentEntry
from cadence_memory.documents import annotations as annotations_module
from cadence_memory.documents import hashes
from cadence_memory.documents.chunker import chunk_markdown
from cadence_memory.documents.parser import parse_file
from cadence_memory.store.interface import Store, StoredDocument

__all__ = ["ReindexError", "ReindexResult", "reindex"]

logger = logging.getLogger(__name__)


class ReindexError(Exception):
    """Raised when a non-optional document file is missing or paths conflict."""


@dataclass(frozen=True, slots=True)
class ReindexResult:
    inserted: tuple[str, ...]
    updated_content: tuple[str, ...]
    updated_metadata_only: tuple[str, ...]
    deleted: tuple[str, ...]
    skipped_optional_missing: tuple[str, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _resolve_entry_path(
    entry: DocumentEntry,
    project_paths: dict[str, Path],
    store_dir: Path,
) -> Path:
    rel = Path(entry.path)
    if entry.project is None:
        base = store_dir
    else:
        try:
            base = project_paths[entry.project]
        except KeyError as exc:
            raise ReindexError(
                f"document {entry.id!r}: project {entry.project!r} is not declared "
                f"in config.projects"
            ) from exc
    candidate = rel if rel.is_absolute() else base / rel
    return candidate.resolve(strict=False)


def _merged_to_hash_dict(
    merged: annotations_module.MergedAnnotation,
) -> dict[str, object]:
    return {
        "kind": merged.kind,
        "title": merged.title,
        "tags": list(merged.tags),
        "related": list(merged.related),
        "project": merged.project,
        "confidence": merged.confidence,
        "last_confirmed_at": merged.last_confirmed_at,
    }


def _classify_entries(
    *,
    config: Config,
    annotations: AnnotationsConfig,
    store: Store,
    store_dir: Path,
    now: Callable[[], datetime],
) -> tuple[list[StoredDocument], ReindexResult]:
    project_paths = {project.name: project.path for project in config.projects}

    planned_upserts: list[StoredDocument] = []
    inserted: list[str] = []
    updated_content: list[str] = []
    updated_metadata_only: list[str] = []
    skipped_optional_missing: list[str] = []
    expected_ids: set[str] = set()
    seen_paths: dict[Path, str] = {}

    for entry in annotations.documents:
        resolved = _resolve_entry_path(entry, project_paths, store_dir)
        if resolved in seen_paths:
            raise ReindexError(
                f"document id {entry.id!r} resolves to the same physical path as "
                f"{seen_paths[resolved]!r} ({resolved})"
            )
        seen_paths[resolved] = entry.id

        expected_ids.add(entry.id)

        if not resolved.is_file():
            if entry.optional:
                skipped_optional_missing.append(entry.id)
                continue
            raise ReindexError(f"document {entry.id!r}: file not found at {resolved}")

        try:
            parsed = parse_file(resolved)
        except (ValueError, OSError) as exc:
            raise ReindexError(f"document {entry.id!r}: cannot read {resolved}: {exc}") from exc
        c_hash = hashes.content_hash(parsed.body)
        f_hash = hashes.frontmatter_hash(parsed.frontmatter_text)

        annotations_entry: dict[str, object] = {
            "kind": entry.kind,
            "title": entry.title,
            "tags": list(entry.tags),
            "related": list(entry.related),
        }
        try:
            merged = annotations_module.merge(
                annotations_entry=annotations_entry,
                frontmatter=parsed.frontmatter,
                h1_title=parsed.h1_title,
                filename_fallback=Path(entry.path).stem,
                defaults_kind=config.defaults.kind,
            )
        except ValueError as exc:
            raise ReindexError(f"document {entry.id!r}: cannot merge annotations: {exc}") from exc
        a_hash = hashes.annotation_hash(_merged_to_hash_dict(merged))

        source_type: Literal["project", "global"] = "global" if entry.project is None else "project"
        try:
            mtime = os.stat(resolved).st_mtime_ns // 1_000_000_000
        except OSError as exc:
            raise ReindexError(f"document {entry.id!r}: cannot stat {resolved}: {exc}") from exc
        new_doc = StoredDocument(
            id=entry.id,
            source_type=source_type,
            project=entry.project,
            abs_path=str(resolved),
            rel_path=entry.path,
            kind=merged.kind,
            title=merged.title,
            body=parsed.body,
            tags=merged.tags,
            related=merged.related,
            content_hash=c_hash,
            frontmatter_hash=f_hash,
            annotation_hash=a_hash,
            mtime=mtime,
            indexed_at=now().isoformat(),
        )

        existing = store.get(entry.id)
        if existing is None:
            inserted.append(entry.id)
            planned_upserts.append(new_doc)
            continue

        if existing.content_hash != c_hash:
            updated_content.append(entry.id)
            planned_upserts.append(new_doc)
            continue

        if (
            existing.frontmatter_hash != f_hash
            or existing.annotation_hash != a_hash
            or existing.abs_path != str(resolved)
            or existing.rel_path != entry.path
        ):
            updated_metadata_only.append(entry.id)
            planned_upserts.append(new_doc)
            continue

    managed_ids = {doc_id for doc_id in store.all_ids() if not doc_id.startswith("eph:")}
    deleted_ids = sorted(managed_ids - expected_ids)

    result = ReindexResult(
        inserted=tuple(sorted(inserted)),
        updated_content=tuple(sorted(updated_content)),
        updated_metadata_only=tuple(sorted(updated_metadata_only)),
        deleted=tuple(deleted_ids),
        skipped_optional_missing=tuple(sorted(skipped_optional_missing)),
    )
    return planned_upserts, result


def reindex(
    *,
    config: Config,
    annotations: AnnotationsConfig,
    store: Store,
    store_dir: Path,
    now: Callable[[], datetime] = _utc_now,
) -> ReindexResult:
    planned_upserts, result = _classify_entries(
        config=config,
        annotations=annotations,
        store=store,
        store_dir=store_dir,
        now=now,
    )
    chunk_rebuild_ids = set(result.inserted) | set(result.updated_content)
    for doc in planned_upserts:
        store.upsert(doc)
        if doc.id in chunk_rebuild_ids:
            store.upsert_chunks(doc.id, chunk_markdown(doc.body))
    for doc_id in result.deleted:
        logger.warning("reindex: deleting orphan document id=%s", doc_id)
        store.delete(doc_id)
    return result
