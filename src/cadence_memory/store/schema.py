"""SQL DDL and init_schema() with WAL and foreign_keys enabled."""

from __future__ import annotations

import contextlib
import sqlite3

DOCUMENTS_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    project TEXT,
    abs_path TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    frontmatter_hash TEXT NOT NULL,
    annotation_hash TEXT NOT NULL,
    mtime INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
)
"""

TAGS_DDL = """
CREATE TABLE IF NOT EXISTS tags (
    doc_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (doc_id, tag),
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
)
"""

RELATIONS_DDL = """
CREATE TABLE IF NOT EXISTS relations (
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id),
    FOREIGN KEY (src_id) REFERENCES documents(id) ON DELETE CASCADE
)
"""

CHUNKS_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    body TEXT NOT NULL,
    chunk_order INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    summary TEXT,
    enrichment TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
)
"""

DOCUMENTS_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    chunk_id UNINDEXED,
    document_id UNINDEXED,
    title,
    heading_path,
    body,
    enrichment,
    tags,
    tokenize='porter unicode61'
)
"""

DISCOVER_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS discover_cache (
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    annotation_json TEXT NOT NULL,
    model TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (path, content_hash)
)
"""

ENRICHMENT_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS enrichment_cache (
    content_hash TEXT PRIMARY KEY,
    enrichment_json TEXT NOT NULL,
    model TEXT NOT NULL,
    generated_at TEXT NOT NULL
)
"""

INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind)",
    "CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project)",
    "CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_discover_cache_path ON discover_cache(path)",
)


def init_schema(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(DOCUMENTS_DDL)
        conn.execute(TAGS_DDL)
        conn.execute(RELATIONS_DDL)
        conn.execute(CHUNKS_DDL)
        conn.execute(DOCUMENTS_FTS_DDL)
        conn.execute(DISCOVER_CACHE_DDL)
        conn.execute(ENRICHMENT_CACHE_DDL)
        for stmt in INDEX_DDL:
            conn.execute(stmt)
        chunk_columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if "summary" not in chunk_columns:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ALTER TABLE chunks ADD COLUMN summary TEXT")
        if "enrichment" not in chunk_columns:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ALTER TABLE chunks ADD COLUMN enrichment TEXT")
        _migrate_documents_fts(conn)


def _migrate_documents_fts(conn: sqlite3.Connection) -> None:
    fts_columns = [
        row[1] for row in conn.execute("PRAGMA table_info(documents_fts)").fetchall()
    ]
    if "enrichment" in fts_columns:
        return
    conn.execute("DROP TABLE IF EXISTS documents_fts")
    conn.execute(DOCUMENTS_FTS_DDL)
    conn.execute(
        """
        INSERT INTO documents_fts (
            chunk_id, document_id, title, heading_path, body, enrichment, tags
        )
        SELECT
            c.id,
            c.document_id,
            d.title,
            REPLACE(REPLACE(REPLACE(c.heading_path, '[', ''), ']', ''), '"', ''),
            c.body,
            COALESCE(c.enrichment, ''),
            COALESCE(
                (SELECT GROUP_CONCAT(t.tag, ' ') FROM tags t WHERE t.doc_id = d.id),
                ''
            )
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        """
    )
