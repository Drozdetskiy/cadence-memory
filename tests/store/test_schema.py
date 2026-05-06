"""Unit tests for init_schema()."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cadence_memory.store.schema import init_schema


def _open(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _master_snapshot(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    return [(t, n, s) for t, n, s in rows]


def test_init_schema_creates_all_tables(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        init_schema(conn)
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
            ).fetchall()
        }
        # FTS5 virtual table appears in sqlite_master with type='table'.
        for expected in (
            "documents",
            "tags",
            "relations",
            "chunks",
            "documents_fts",
            "discover_cache",
        ):
            assert expected in names
    finally:
        conn.close()


def test_init_schema_creates_chunk_shaped_fts(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        init_schema(conn)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(documents_fts)").fetchall()]
        for expected in ("chunk_id", "document_id", "title", "heading_path", "body", "tags"):
            assert expected in cols
    finally:
        conn.close()


def test_init_schema_creates_chunks_table_with_columns(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        init_schema(conn)
        cols = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        for expected in (
            "id",
            "document_id",
            "slug",
            "heading_path",
            "body",
            "chunk_order",
            "content_hash",
            "summary",
        ):
            assert expected in cols
    finally:
        conn.close()


def test_init_schema_upgrades_legacy_chunks_table(tmp_path: Path) -> None:
    legacy_ddl = """
    CREATE TABLE chunks (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        slug TEXT NOT NULL,
        heading_path TEXT NOT NULL,
        body TEXT NOT NULL,
        chunk_order INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    )
    """
    legacy_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(legacy_path))
    try:
        legacy.execute(legacy_ddl)
        legacy.execute(
            "INSERT INTO chunks (id, document_id, slug, heading_path, body, "
            "chunk_order, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("c1", "d1", "intro", "[]", "legacy body", 0, "hash"),
        )
        legacy.commit()
    finally:
        legacy.close()

    conn = sqlite3.connect(str(legacy_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        assert "summary" in cols
        row = conn.execute(
            "SELECT id, document_id, slug, body, summary FROM chunks WHERE id = ?", ("c1",)
        ).fetchone()
        assert row == ("c1", "d1", "intro", "legacy body", None)

        snapshot_after_first = _master_snapshot(conn)
        init_schema(conn)
        snapshot_after_second = _master_snapshot(conn)
        assert snapshot_after_first == snapshot_after_second
    finally:
        conn.close()

    fresh = _open(tmp_path)
    try:
        init_schema(fresh)
        fresh_cols = {row[1] for row in fresh.execute("PRAGMA table_info(chunks)").fetchall()}
    finally:
        fresh.close()

    assert cols == fresh_cols


def test_init_schema_creates_all_indexes(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        init_schema(conn)
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        for expected in (
            "idx_documents_kind",
            "idx_documents_project",
            "idx_documents_source",
            "idx_chunks_document",
            "idx_discover_cache_path",
        ):
            assert expected in names
    finally:
        conn.close()


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        init_schema(conn)
        before = _master_snapshot(conn)
        init_schema(conn)
        after = _master_snapshot(conn)
        assert before == after
    finally:
        conn.close()
