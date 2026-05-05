"""Unit tests for SqliteStore: upsert/get, delete cascade, list, query, all_ids."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

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


def test_upsert_overwrites_scalar_columns_and_refreshes_fts(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        original = _make_doc(
            title="Old Title",
            body="old body content uniqueoldtoken",
            kind="pattern",
            mtime=1700000000,
        )
        store.upsert(original)

        revised = _make_doc(
            title="New Title",
            body="new body content uniquenewtoken",
            kind="howto",
            mtime=1800000000,
        )
        store.upsert(revised)

        got = store.get(original.id)
        assert got == revised

        stale_hits = store.query("uniqueoldtoken")
        assert stale_hits == []

        fresh_hits = store.query("uniquenewtoken")
        assert {d.id for d in fresh_hits} == {original.id}
    finally:
        store.close()


def test_delete_removes_document_and_cascades(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    store = SqliteStore(db_path)
    try:
        doc = _make_doc(tags=("a", "b"), related=("x:a",))
        store.upsert(doc)
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
        fts_count = inspect.execute(
            "SELECT COUNT(*) FROM documents_fts WHERE id = ?", ("proj:doc.md",)
        ).fetchone()[0]
        assert tag_count == 0
        assert rel_count == 0
        assert fts_count == 0
    finally:
        inspect.close()


def test_delete_does_not_touch_other_documents(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        keep = _make_doc(
            id="proj:keep.md",
            title="Keeper title",
            tags=("shared", "keep"),
            related=("proj:other.md",),
        )
        gone = _make_doc(
            id="proj:gone.md",
            title="Doomed title",
            tags=("shared", "gone"),
            related=("proj:other.md",),
        )
        store.upsert(keep)
        store.upsert(gone)
        store.delete(gone.id)

        survivor = store.get(keep.id)
        assert survivor == keep

        hits = store.query("Keeper")
        assert {d.id for d in hits} == {"proj:keep.md"}
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


def test_query_matches_title_body_and_tags_with_filters(tmp_path: Path) -> None:
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

        all_token = store.query("tokens")
        assert {d.id for d in all_token} == {"alpha:auth.md", "alpha:db.md", "beta:auth.md"}

        by_tag = store.query("security")
        assert {d.id for d in by_tag} == {"alpha:auth.md", "beta:auth.md"}

        by_kind = store.query("tokens", kind="pattern")
        assert {d.id for d in by_kind} == {"alpha:auth.md", "beta:auth.md"}

        by_project = store.query("tokens", project="alpha")
        assert {d.id for d in by_project} == {"alpha:auth.md", "alpha:db.md"}

        limited = store.query("tokens", limit=1)
        assert len(limited) == 1

        empty = store.query("nonexistentterm")
        assert empty == []
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
