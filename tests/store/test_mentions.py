"""Unit tests for SqliteStore mentions: upsert/get round-trip, replace, cascade, backlinks."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from cadence_memory.documents.chunker import Chunk
from cadence_memory.store.interface import Mention, StoredDocument
from cadence_memory.store.sqlite_store import SqliteStore


def _make_doc(
    *,
    id: str = "proj:doc.md",
    source_type: Literal["project", "global", "ephemeral"] = "project",
    project: str | None = "proj",
    abs_path: str = "/abs/proj/doc.md",
    rel_path: str = "doc.md",
    kind: str = "pattern",
    title: str = "Doc Title",
    body: str = "doc body content",
    tags: tuple[str, ...] = (),
    related: tuple[str, ...] = (),
    content_hash: str = "c" * 64,
    frontmatter_hash: str = "f" * 64,
    annotation_hash: str = "a" * 64,
    mtime: int = 1700000000,
    indexed_at: str = "2026-01-01T00:00:00Z",
) -> StoredDocument:
    return StoredDocument(
        id=id,
        source_type=source_type,
        project=project,
        abs_path=abs_path,
        rel_path=rel_path,
        kind=kind,
        title=title,
        body=body,
        tags=tags,
        related=related,
        content_hash=content_hash,
        frontmatter_hash=frontmatter_hash,
        annotation_hash=annotation_hash,
        mtime=mtime,
        indexed_at=indexed_at,
    )


def _seed_doc_with_chunk(
    store: SqliteStore,
    *,
    doc_id: str = "proj:doc.md",
    title: str = "Doc Title",
    body: str = "preamble body",
    slug: str = "_preamble",
) -> str:
    doc = _make_doc(id=doc_id, title=title, body=body)
    store.upsert(doc)
    store.upsert_chunks(doc.id, [Chunk(slug=slug, heading_path=(), body=body, order=0)])
    return f"{doc.id}#{slug}"


def test_upsert_and_get_round_trip(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        chunk_id = _seed_doc_with_chunk(store)
        mentions = [
            Mention(target="src/foo.py", target_kind="code", line_range=None),
            Mention(target="src/foo.py", target_kind="code", line_range="100-200"),
            Mention(target="GET /v1/users", target_kind="endpoint"),
        ]
        store.upsert_mentions(chunk_id, mentions)
        got = store.get_mentions(chunk_id)
        assert set(got) == set(mentions)
    finally:
        store.close()


def test_upsert_replaces_prior_rows(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        chunk_id = _seed_doc_with_chunk(store)
        store.upsert_mentions(
            chunk_id,
            [
                Mention(target="src/old.py", target_kind="code"),
                Mention(target="OldEntity", target_kind="schema"),
            ],
        )
        store.upsert_mentions(
            chunk_id,
            [Mention(target="src/new.py", target_kind="code")],
        )
        got = store.get_mentions(chunk_id)
        assert got == [Mention(target="src/new.py", target_kind="code")]
    finally:
        store.close()


def test_upsert_with_empty_list_clears_rows(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        chunk_id = _seed_doc_with_chunk(store)
        store.upsert_mentions(chunk_id, [Mention(target="src/x.py", target_kind="code")])
        store.upsert_mentions(chunk_id, [])
        assert store.get_mentions(chunk_id) == []
    finally:
        store.close()


def test_get_mentions_returns_deterministic_order(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        chunk_id = _seed_doc_with_chunk(store)
        store.upsert_mentions(
            chunk_id,
            [
                Mention(target="src/zz.py", target_kind="code"),
                Mention(target="POST /v1/users", target_kind="endpoint"),
                Mention(target="src/aa.py", target_kind="code", line_range="10"),
                Mention(target="src/aa.py", target_kind="code"),
                Mention(target="BillingEntity", target_kind="schema"),
            ],
        )
        got = store.get_mentions(chunk_id)
        assert got == [
            Mention(target="src/aa.py", target_kind="code"),
            Mention(target="src/aa.py", target_kind="code", line_range="10"),
            Mention(target="src/zz.py", target_kind="code"),
            Mention(target="POST /v1/users", target_kind="endpoint"),
            Mention(target="BillingEntity", target_kind="schema"),
        ]
    finally:
        store.close()


def test_delete_document_cascades_to_mentions(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    store = SqliteStore(db_path)
    try:
        chunk_id = _seed_doc_with_chunk(store)
        store.upsert_mentions(chunk_id, [Mention(target="src/foo.py", target_kind="code")])
        store.delete("proj:doc.md")
        assert store.get_mentions(chunk_id) == []
    finally:
        store.close()

    inspect = sqlite3.connect(str(db_path))
    inspect.execute("PRAGMA foreign_keys = ON")
    try:
        count = inspect.execute(
            "SELECT COUNT(*) FROM mentions WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()[0]
        assert count == 0
    finally:
        inspect.close()


def test_find_backlinks_returns_matching_chunks(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        c1 = _seed_doc_with_chunk(store, doc_id="proj:a.md", title="Doc A")
        c2 = _seed_doc_with_chunk(store, doc_id="proj:b.md", title="Doc B")
        c3 = _seed_doc_with_chunk(store, doc_id="proj:c.md", title="Doc C")

        store.upsert_mentions(c1, [Mention(target="src/foo.py", target_kind="code")])
        store.upsert_mentions(
            c2,
            [
                Mention(target="src/foo.py", target_kind="code", line_range="10"),
                Mention(target="BillingEntity", target_kind="schema"),
            ],
        )
        store.upsert_mentions(c3, [Mention(target="src/other.py", target_kind="code")])

        hits = store.find_backlinks("src/foo.py")
        assert [c.id for c in hits] == [c1, c2]

        scoped = store.find_backlinks("src/foo.py", target_kind="code")
        assert [c.id for c in scoped] == [c1, c2]

        wrong_kind = store.find_backlinks("src/foo.py", target_kind="schema")
        assert wrong_kind == []
    finally:
        store.close()


def test_find_backlinks_empty_for_unknown_target(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        chunk_id = _seed_doc_with_chunk(store)
        store.upsert_mentions(chunk_id, [Mention(target="src/foo.py", target_kind="code")])
        assert store.find_backlinks("src/missing.py") == []
    finally:
        store.close()


def test_find_backlinks_dedupes_chunks_with_repeated_target(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        chunk_id = _seed_doc_with_chunk(store)
        store.upsert_mentions(
            chunk_id,
            [
                Mention(target="src/foo.py", target_kind="code"),
                Mention(target="src/foo.py", target_kind="code", line_range="10"),
                Mention(target="src/foo.py", target_kind="code", line_range="20-30"),
            ],
        )
        hits = store.find_backlinks("src/foo.py")
        assert [c.id for c in hits] == [chunk_id]
    finally:
        store.close()


def test_upsert_against_missing_chunk_id_raises(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.upsert_mentions(
                "proj:nope.md#missing",
                [Mention(target="src/foo.py", target_kind="code")],
            )
    finally:
        store.close()
