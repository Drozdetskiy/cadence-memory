"""Copy/symlink/inline ephemeral docs with source_type='ephemeral'."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cadence_memory.documents import annotations as annotations_module
from cadence_memory.documents import hashes
from cadence_memory.documents.chunker import chunk_markdown
from cadence_memory.documents.ids import build_ephemeral_id
from cadence_memory.documents.ids import parse as parse_id
from cadence_memory.documents.parser import parse_text
from cadence_memory.store.interface import Store, StoredDocument

__all__ = [
    "EphemeralAddOptions",
    "EphemeralExists",
    "add",
    "clear",
    "list_",
    "remove",
]


class EphemeralExists(Exception):
    """Raised when add() is called for an id that already exists in the store."""

    def __init__(self, doc_id: str) -> None:
        super().__init__(f"ephemeral document already exists: {doc_id}")
        self.doc_id = doc_id


@dataclass(frozen=True, slots=True)
class EphemeralAddOptions:
    source: Path | None = None
    eph_id: str | None = None
    kind: str = "task"
    title: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    use_symlink: bool = False
    inline_text: str | None = None


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(stem: str) -> str:
    lowered = stem.lower()
    collapsed = _NON_ALNUM.sub("-", lowered).strip("-")
    if not collapsed:
        raise ValueError(f"cannot derive slug from {stem!r}: no alphanumeric characters")
    return collapsed


def _utc_now() -> datetime:
    return datetime.now(UTC)


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


def add(
    opts: EphemeralAddOptions,
    *,
    store: Store,
    store_dir: Path,
    now: Callable[[], datetime] = _utc_now,
) -> StoredDocument:
    if opts.inline_text is not None:
        if opts.eph_id is None:
            raise ValueError("inline mode requires an explicit ephemeral id")
    elif opts.source is None:
        raise ValueError("add() requires either a source path or inline_text")

    if opts.eph_id is not None:
        name = opts.eph_id
    else:
        assert opts.source is not None
        name = _slugify(opts.source.stem)

    doc_id = build_ephemeral_id(name)

    if store.get(doc_id) is not None:
        raise EphemeralExists(doc_id)

    ephemeral_dir = store_dir / "ephemeral"
    ephemeral_dir.mkdir(parents=True, exist_ok=True)
    target = ephemeral_dir / f"{name}.md"

    if opts.inline_text is not None:
        if target.is_symlink() or target.exists():
            target.unlink()
        target.write_text(opts.inline_text, encoding="utf-8")
    elif opts.use_symlink:
        assert opts.source is not None
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(opts.source.resolve())
    else:
        assert opts.source is not None
        if target.is_symlink():
            target.unlink()
        shutil.copyfile(opts.source, target)

    text = target.read_text(encoding="utf-8")
    parsed = parse_text(text)

    annotations_entry: dict[str, object] = {
        "kind": opts.kind,
        "title": opts.title,
        "tags": list(opts.tags),
        "related": [],
    }
    merged = annotations_module.merge(
        annotations_entry=annotations_entry,
        frontmatter=parsed.frontmatter,
        h1_title=parsed.h1_title,
        filename_fallback=name,
        defaults_kind="task",
    )

    c_hash = hashes.content_hash(parsed.body)
    f_hash = hashes.frontmatter_hash(parsed.frontmatter_text)
    a_hash = hashes.annotation_hash(_merged_to_hash_dict(merged))

    mtime = os.stat(target).st_mtime_ns // 1_000_000_000

    doc = StoredDocument(
        id=doc_id,
        source_type="ephemeral",
        project=None,
        abs_path=str(target.resolve()),
        rel_path=f"ephemeral/{name}.md",
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
    store.upsert(doc)
    store.upsert_chunks(doc.id, chunk_markdown(doc.body, kind=merged.kind, doc_id=doc.id))
    return doc


def list_(store: Store) -> list[StoredDocument]:
    return store.list(source_type="ephemeral")


def remove(eph_id: str, *, store: Store, store_dir: Path) -> None:
    parsed = parse_id(eph_id)
    if parsed.source_type != "ephemeral":
        raise ValueError(f"not an ephemeral document id: {eph_id!r}")
    name = eph_id[len("eph:") :]
    target = store_dir / "ephemeral" / f"{name}.md"
    target.unlink(missing_ok=True)
    store.delete(eph_id)


def clear(*, store: Store, store_dir: Path) -> int:
    docs = list_(store)
    for doc in docs:
        remove(doc.id, store=store, store_dir=store_dir)
    return len(docs)
