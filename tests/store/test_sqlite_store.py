"""Unit tests for SqliteStore: upsert/get, delete cascade, list, chunk APIs, query."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from cadence_memory.documents.chunker import Chunk
from cadence_memory.store.interface import StoredDocument
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


def _whole_body_chunk(body: str) -> Chunk:
    return Chunk(slug="_preamble", heading_path=(), body=body, order=0)


def test_upsert_and_get_round_trip(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        doc = _make_doc(
            tags=("zeta", "alpha", "mike"),
            related=("proj:zeta.md", "proj:alpha.md", "proj:mike.md"),
        )
        store.upsert(doc)
        got = store.get(doc.id)
        assert got == doc
        assert got is not None
        assert got.tags == ("zeta", "alpha", "mike")
        assert got.related == ("proj:zeta.md", "proj:alpha.md", "proj:mike.md")
    finally:
        store.close()


def test_get_returns_none_for_missing(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        assert store.get("proj:missing.md") is None
    finally:
        store.close()


def test_upsert_clears_stale_tags_and_relations(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        doc = _make_doc(tags=("a", "b"), related=("x:a", "x:b"))
        store.upsert(doc)

        updated = _make_doc(tags=("b", "c"), related=("y:c",))
        store.upsert(updated)

        got = store.get(doc.id)
        assert got is not None
        assert got.tags == ("b", "c")
        assert got.related == ("y:c",)
    finally:
        store.close()


def test_upsert_preserves_chunks_when_called_twice(tmp_path: Path) -> None:
    """Upsert no longer touches chunks/FTS; chunk lifecycle is owned by upsert_chunks."""
    store = SqliteStore(tmp_path / "test.db")
    try:
        doc = _make_doc(body="body content")
        store.upsert(doc)
        store.upsert_chunks(
            doc.id, [Chunk(slug="_preamble", heading_path=(), body="hello world", order=0)]
        )
        # Re-upsert the document (e.g. metadata-only change). Chunks must remain.
        store.upsert(_make_doc(title="New Title"))
        chunks = store.get_chunks(doc.id)
        assert [c.slug for c in chunks] == ["_preamble"]
    finally:
        store.close()


def test_upsert_refreshes_fts_title_and_tags(tmp_path: Path) -> None:
    """Metadata-only upsert must keep denormalised FTS title/tags in sync."""
    store = SqliteStore(tmp_path / "test.db")
    try:
        doc = _make_doc(title="OriginalTitle", tags=("origtag",))
        store.upsert(doc)
        store.upsert_chunks(doc.id, [_whole_body_chunk("body")])

        assert {c.document_id for c in store.query("OriginalTitle")} == {doc.id}
        assert {c.document_id for c in store.query("origtag")} == {doc.id}

        store.upsert(_make_doc(title="RenamedTitle", tags=("newtag",)))

        assert store.query("OriginalTitle") == []
        assert store.query("origtag") == []
        assert {c.document_id for c in store.query("RenamedTitle")} == {doc.id}
        assert {c.document_id for c in store.query("newtag")} == {doc.id}
    finally:
        store.close()


def test_delete_removes_document_and_cascades(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    store = SqliteStore(db_path)
    try:
        doc = _make_doc(tags=("a", "b"), related=("x:a",))
        store.upsert(doc)
        store.upsert_chunks(doc.id, [_whole_body_chunk("body content")])
        store.delete(doc.id)
        assert store.get(doc.id) is None
    finally:
        store.close()

    inspect = sqlite3.connect(str(db_path))
    inspect.execute("PRAGMA foreign_keys = ON")
    try:
        tag_count = inspect.execute(
            "SELECT COUNT(*) FROM tags WHERE doc_id = ?", ("proj:doc.md",)
        ).fetchone()[0]
        rel_count = inspect.execute(
            "SELECT COUNT(*) FROM relations WHERE src_id = ?", ("proj:doc.md",)
        ).fetchone()[0]
        chunk_count = inspect.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", ("proj:doc.md",)
        ).fetchone()[0]
        fts_count = inspect.execute(
            "SELECT COUNT(*) FROM documents_fts WHERE document_id = ?", ("proj:doc.md",)
        ).fetchone()[0]
        assert tag_count == 0
        assert rel_count == 0
        assert chunk_count == 0
        assert fts_count == 0
    finally:
        inspect.close()


def test_delete_does_not_touch_other_documents(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        keep = _make_doc(
            id="proj:keep.md",
            title="Keeper title",
            body="keep body keepertoken",
            tags=("shared", "keep"),
            related=("proj:other.md",),
        )
        gone = _make_doc(
            id="proj:gone.md",
            title="Doomed title",
            body="gone body",
            tags=("shared", "gone"),
            related=("proj:other.md",),
        )
        store.upsert(keep)
        store.upsert_chunks(keep.id, [_whole_body_chunk(keep.body)])
        store.upsert(gone)
        store.upsert_chunks(gone.id, [_whole_body_chunk(gone.body)])

        store.delete(gone.id)

        survivor = store.get(keep.id)
        assert survivor == keep

        hits = store.query("keepertoken")
        assert {c.document_id for c in hits} == {"proj:keep.md"}
    finally:
        store.close()


def test_list_filters_by_kind_project_and_source_type(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        a = _make_doc(id="alpha:one.md", project="alpha", kind="pattern")
        b = _make_doc(id="alpha:two.md", project="alpha", kind="howto")
        c = _make_doc(id="beta:one.md", project="beta", kind="pattern")
        e = _make_doc(
            id="eph:1",
            source_type="ephemeral",
            project=None,
            kind="task",
        )
        for d in (a, b, c, e):
            store.upsert(d)

        by_kind = store.list(kind="pattern")
        assert {d.id for d in by_kind} == {"alpha:one.md", "beta:one.md"}

        by_project = store.list(project="alpha")
        assert {d.id for d in by_project} == {"alpha:one.md", "alpha:two.md"}

        by_source = store.list(source_type="ephemeral")
        assert {d.id for d in by_source} == {"eph:1"}

        combined = store.list(kind="pattern", project="alpha")
        assert {d.id for d in combined} == {"alpha:one.md"}

        none = store.list(kind="pattern", project="missing")
        assert none == []
    finally:
        store.close()


def test_upsert_chunks_writes_rows_and_fts_in_order(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        doc = _make_doc(tags=("security", "auth"))
        store.upsert(doc)
        chunks = [
            Chunk(slug="_preamble", heading_path=(), body="preamble body", order=0),
            Chunk(
                slug="overview",
                heading_path=("Overview",),
                body="# Overview\noverview body uniqtoken",
                order=1,
            ),
            Chunk(
                slug="details",
                heading_path=("Overview", "Details"),
                body="## Details\ndetails body",
                order=2,
            ),
        ]
        store.upsert_chunks(doc.id, chunks)

        got = store.get_chunks(doc.id)
        assert [c.slug for c in got] == ["_preamble", "overview", "details"]
        assert got[1].id == "proj:doc.md#overview"
        assert got[1].heading_path == ("Overview",)
        assert got[2].heading_path == ("Overview", "Details")
        assert got[0].document_title == "Doc Title"
        assert got[0].document_kind == "pattern"
        assert got[0].document_project == "proj"
        assert all(len(c.content_hash) == 64 for c in got)
    finally:
        store.close()


def test_upsert_chunks_replaces_old_rows_without_orphans(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    store = SqliteStore(db_path)
    try:
        doc = _make_doc(tags=("security",))
        store.upsert(doc)
        first = [
            Chunk(slug="_preamble", heading_path=(), body="first preamble", order=0),
            Chunk(
                slug="overview",
                heading_path=("Overview",),
                body="# Overview\nfirst overview",
                order=1,
            ),
        ]
        store.upsert_chunks(doc.id, first)

        second = [
            Chunk(slug="_preamble", heading_path=(), body="second preamble", order=0),
            Chunk(
                slug="rewritten",
                heading_path=("Rewritten",),
                body="# Rewritten\nbrand new body",
                order=1,
            ),
        ]
        store.upsert_chunks(doc.id, second)

        got = store.get_chunks(doc.id)
        assert [c.slug for c in got] == ["_preamble", "rewritten"]

        chunk_count = store._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc.id,)
        ).fetchone()[0]
        fts_count = store._conn.execute(
            "SELECT COUNT(*) FROM documents_fts WHERE document_id = ?", (doc.id,)
        ).fetchone()[0]
        assert chunk_count == 2
        assert fts_count == 2
    finally:
        store.close()


def test_upsert_chunks_raises_for_unknown_document(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        with pytest.raises(ValueError):
            store.upsert_chunks("proj:missing.md", [_whole_body_chunk("body")])
    finally:
        store.close()


def test_get_chunk_returns_chunk_or_none(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        doc = _make_doc()
        store.upsert(doc)
        store.upsert_chunks(
            doc.id,
            [
                Chunk(slug="_preamble", heading_path=(), body="preamble", order=0),
                Chunk(slug="overview", heading_path=("Overview",), body="# Overview", order=1),
            ],
        )
        got = store.get_chunk("proj:doc.md#overview")
        assert got is not None
        assert got.slug == "overview"
        assert got.document_id == "proj:doc.md"

        assert store.get_chunk("proj:doc.md#missing") is None
        assert store.get_chunk("proj:nope.md#anything") is None
    finally:
        store.close()


def test_query_returns_stored_chunks_with_denormalised_doc_fields(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        a = _make_doc(
            id="alpha:auth.md",
            project="alpha",
            kind="pattern",
            title="Authentication patterns",
            body="JWT and session tokens explained",
            tags=("security",),
        )
        b = _make_doc(
            id="alpha:db.md",
            project="alpha",
            kind="howto",
            title="Database setup",
            body="Migrating with alembic and tokens for csrf",
            tags=("backend",),
        )
        c = _make_doc(
            id="beta:auth.md",
            project="beta",
            kind="pattern",
            title="Login flow",
            body="OAuth tokens for third parties",
            tags=("security",),
        )
        for d in (a, b, c):
            store.upsert(d)
            store.upsert_chunks(d.id, [_whole_body_chunk(d.body)])

        all_token = store.query("tokens")
        assert {c.document_id for c in all_token} == {
            "alpha:auth.md",
            "alpha:db.md",
            "beta:auth.md",
        }
        sample = next(c for c in all_token if c.document_id == "alpha:auth.md")
        assert sample.document_title == "Authentication patterns"
        assert sample.document_kind == "pattern"
        assert sample.document_project == "alpha"

        by_tag = store.query("security")
        assert {c.document_id for c in by_tag} == {"alpha:auth.md", "beta:auth.md"}

        by_kind = store.query("tokens", kind="pattern")
        assert {c.document_id for c in by_kind} == {"alpha:auth.md", "beta:auth.md"}

        by_project = store.query("tokens", project="alpha")
        assert {c.document_id for c in by_project} == {"alpha:auth.md", "alpha:db.md"}

        limited = store.query("tokens", limit=1)
        assert len(limited) == 1

        empty = store.query("nonexistentterm")
        assert empty == []
    finally:
        store.close()


def test_query_returns_per_chunk_hits_for_same_document(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        doc = _make_doc(
            id="alpha:big.md",
            project="alpha",
            kind="pattern",
            title="Big doc",
            body="entire body",
            tags=("docs",),
        )
        store.upsert(doc)
        store.upsert_chunks(
            doc.id,
            [
                Chunk(slug="_preamble", heading_path=(), body="first chunk uniqterm", order=0),
                Chunk(
                    slug="other",
                    heading_path=("Other",),
                    body="# Other\nsecond chunk uniqterm",
                    order=1,
                ),
            ],
        )

        hits = store.query("uniqterm")
        assert len(hits) == 2
        assert {c.slug for c in hits} == {"_preamble", "other"}
    finally:
        store.close()


def test_all_ids_returns_full_set(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        ids = ("proj:a.md", "proj:b.md", "proj:c.md")
        for doc_id in ids:
            store.upsert(_make_doc(id=doc_id))
        assert store.all_ids() == set(ids)
    finally:
        store.close()


def test_ephemeral_document_round_trip(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        doc = _make_doc(
            id="eph:abc123",
            source_type="ephemeral",
            project=None,
            abs_path="/store/ephemeral/abc123.md",
            rel_path="ephemeral/abc123.md",
            kind="task",
            tags=("draft",),
        )
        store.upsert(doc)

        got = store.get(doc.id)
        assert got == doc

        listed = store.list(source_type="ephemeral")
        assert listed == [doc]

        projects_only = store.list(source_type="project")
        assert projects_only == []
    finally:
        store.close()
