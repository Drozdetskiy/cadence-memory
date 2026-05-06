"""SqliteStore implementing upsert, delete, get, list, FTS5 query, and all_ids."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from cadence_memory.store.interface import StoredDocument
from cadence_memory.store.schema import init_schema

__all__ = ["SqliteStore"]


_SourceType = Literal["project", "global", "ephemeral"]

type _DocList = list[StoredDocument]


class SqliteStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        init_schema(self._conn)

    def upsert(self, doc: StoredDocument) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO documents (
                    id, source_type, project, abs_path, rel_path,
                    kind, title, body,
                    content_hash, frontmatter_hash, annotation_hash,
                    mtime, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc.id,
                    doc.source_type,
                    doc.project,
                    doc.abs_path,
                    doc.rel_path,
                    doc.kind,
                    doc.title,
                    doc.body,
                    doc.content_hash,
                    doc.frontmatter_hash,
                    doc.annotation_hash,
                    doc.mtime,
                    doc.indexed_at,
                ),
            )
            self._conn.execute("DELETE FROM tags WHERE doc_id = ?", (doc.id,))
            if doc.tags:
                self._conn.executemany(
                    "INSERT INTO tags (doc_id, tag) VALUES (?, ?)",
                    [(doc.id, tag) for tag in doc.tags],
                )
            self._conn.execute("DELETE FROM relations WHERE src_id = ?", (doc.id,))
            if doc.related:
                self._conn.executemany(
                    "INSERT INTO relations (src_id, dst_id) VALUES (?, ?)",
                    [(doc.id, dst) for dst in doc.related],
                )
            self._conn.execute("DELETE FROM documents_fts WHERE id = ?", (doc.id,))
            self._conn.execute(
                "INSERT INTO documents_fts (id, title, body, tags) VALUES (?, ?, ?, ?)",
                (doc.id, doc.title, doc.body, " ".join(doc.tags)),
            )

    def delete(self, doc_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.execute("DELETE FROM documents_fts WHERE id = ?", (doc_id,))

    def get(self, doc_id: str) -> StoredDocument | None:
        row = self._conn.execute(
            """
            SELECT id, source_type, project, abs_path, rel_path,
                   kind, title, body,
                   content_hash, frontmatter_hash, annotation_hash,
                   mtime, indexed_at
            FROM documents
            WHERE id = ?
            """,
            (doc_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_doc(row)

    def list(
        self,
        *,
        kind: str | None = None,
        project: str | None = None,
        source_type: str | None = None,
    ) -> _DocList:
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        if source_type is not None:
            clauses.append("source_type = ?")
            params.append(source_type)
        sql = (
            "SELECT id, source_type, project, abs_path, rel_path, "
            "kind, title, body, content_hash, frontmatter_hash, "
            "annotation_hash, mtime, indexed_at FROM documents"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_doc(row) for row in rows]

    def query(
        self,
        text: str,
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> _DocList:
        clauses: list[str] = ["documents_fts MATCH ?"]
        params: list[object] = [text]
        if kind is not None:
            clauses.append("d.kind = ?")
            params.append(kind)
        if project is not None:
            clauses.append("d.project = ?")
            params.append(project)
        sql = (
            "SELECT d.id, d.source_type, d.project, d.abs_path, d.rel_path, "
            "d.kind, d.title, d.body, d.content_hash, d.frontmatter_hash, "
            "d.annotation_hash, d.mtime, d.indexed_at "
            "FROM documents_fts "
            "JOIN documents d ON d.id = documents_fts.id "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY rank "
            "LIMIT ?"
        )
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_doc(row) for row in rows]

    def all_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT id FROM documents").fetchall()
        return {row[0] for row in rows}

    def discover_cache_get(self, *, path: str, content_hash: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT annotation_json FROM discover_cache WHERE path = ? AND content_hash = ?",
            (path, content_hash),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(cast(str, row[0]))
        return cast("dict[str, object]", payload)

    def discover_cache_put(
        self,
        *,
        path: str,
        content_hash: str,
        annotation_json: str,
        model: str,
    ) -> None:
        generated_at = datetime.now(tz=UTC).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO discover_cache "
                "(path, content_hash, annotation_json, model, generated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (path, content_hash, annotation_json, model, generated_at),
            )

    def discover_cache_clear(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM discover_cache")

    def close(self) -> None:
        self._conn.close()

    def _row_to_doc(self, row: tuple[object, ...]) -> StoredDocument:
        doc_id = cast(str, row[0])
        tag_rows = self._conn.execute(
            "SELECT tag FROM tags WHERE doc_id = ? ORDER BY rowid",
            (doc_id,),
        ).fetchall()
        relation_rows = self._conn.execute(
            "SELECT dst_id FROM relations WHERE src_id = ? ORDER BY rowid",
            (doc_id,),
        ).fetchall()
        return StoredDocument(
            id=doc_id,
            source_type=cast(_SourceType, row[1]),
            project=cast("str | None", row[2]),
            abs_path=cast(str, row[3]),
            rel_path=cast(str, row[4]),
            kind=cast(str, row[5]),
            title=cast(str, row[6]),
            body=cast(str, row[7]),
            tags=tuple(cast(str, r[0]) for r in tag_rows),
            related=tuple(cast(str, r[0]) for r in relation_rows),
            content_hash=cast(str, row[8]),
            frontmatter_hash=cast(str, row[9]),
            annotation_hash=cast(str, row[10]),
            mtime=cast(int, row[11]),
            indexed_at=cast(str, row[12]),
        )
