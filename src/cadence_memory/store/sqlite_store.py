"""SqliteStore implementing upsert, delete, get, list, FTS5 query, and chunk APIs."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from cadence_memory.documents.chunker import Chunk
from cadence_memory.documents.hashes import chunk_content_hash
from cadence_memory.query.identifiers import QueryIdentifiers, extract_identifiers
from cadence_memory.store.interface import Mention, MentionKind, StoredChunk, StoredDocument
from cadence_memory.store.schema import init_schema

__all__ = ["SqliteStore"]


_SourceType = Literal["project", "global", "ephemeral"]

_BOOST_PER_KIND: float = 5.0

type _DocList = list[StoredDocument]
type _ChunkList = list[StoredChunk]
type _MentionList = list[Mention]


class SqliteStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        init_schema(self._conn)

    def upsert(self, doc: StoredDocument) -> None:
        with self._conn:
            existing = self._conn.execute(
                "SELECT 1 FROM documents WHERE id = ?", (doc.id,)
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    """
                    INSERT INTO documents (
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
            else:
                self._conn.execute(
                    """
                    UPDATE documents SET
                        source_type = ?, project = ?, abs_path = ?, rel_path = ?,
                        kind = ?, title = ?, body = ?,
                        content_hash = ?, frontmatter_hash = ?, annotation_hash = ?,
                        mtime = ?, indexed_at = ?
                    WHERE id = ?
                    """,
                    (
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
                        doc.id,
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
            self._conn.execute(
                "UPDATE documents_fts SET title = ?, tags = ? WHERE document_id = ?",
                (doc.title, " ".join(doc.tags), doc.id),
            )

    def delete(self, doc_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.execute("DELETE FROM documents_fts WHERE document_id = ?", (doc_id,))

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

    def upsert_chunks(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        doc_row = self._conn.execute(
            "SELECT title FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if doc_row is None:
            raise ValueError(f"document not found: {document_id}")
        doc_title = cast(str, doc_row[0])
        tag_rows = self._conn.execute(
            "SELECT tag FROM tags WHERE doc_id = ? ORDER BY rowid",
            (document_id,),
        ).fetchall()
        tags_blob = " ".join(cast(str, r[0]) for r in tag_rows)

        with self._conn:
            self._conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            self._conn.execute("DELETE FROM documents_fts WHERE document_id = ?", (document_id,))
            for chunk in chunks:
                chunk_id = f"{document_id}#{chunk.slug}"
                heading_path_json = json.dumps(list(chunk.heading_path), ensure_ascii=False)
                content_hash = chunk_content_hash(chunk.slug, chunk.body)
                self._conn.execute(
                    """
                    INSERT INTO chunks (
                        id, document_id, slug, heading_path, body,
                        chunk_order, content_hash, summary, enrichment
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        document_id,
                        chunk.slug,
                        heading_path_json,
                        chunk.body,
                        chunk.order,
                        content_hash,
                        chunk.summary,
                        None,
                    ),
                )
                heading_path_fts = " ".join(chunk.heading_path)
                self._conn.execute(
                    """
                    INSERT INTO documents_fts (
                        chunk_id, document_id, title, heading_path, body, enrichment, tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        document_id,
                        doc_title,
                        heading_path_fts,
                        chunk.body,
                        "",
                        tags_blob,
                    ),
                )

    def get_chunks(self, document_id: str) -> _ChunkList:
        rows = self._conn.execute(
            """
            SELECT c.id, c.document_id, c.slug, c.heading_path, c.body,
                   c.chunk_order, c.content_hash,
                   d.title, d.kind, d.project, c.summary, c.enrichment
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.document_id = ?
            ORDER BY c.chunk_order
            """,
            (document_id,),
        ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def get_chunk(self, chunk_id: str) -> StoredChunk | None:
        row = self._conn.execute(
            """
            SELECT c.id, c.document_id, c.slug, c.heading_path, c.body,
                   c.chunk_order, c.content_hash,
                   d.title, d.kind, d.project, c.summary, c.enrichment
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_chunk(row)

    def query(
        self,
        text: str,
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
        boost: bool = True,
    ) -> _ChunkList:
        clauses: list[str] = ["documents_fts MATCH ?"]
        params: list[object] = [text]
        if kind is not None:
            clauses.append("d.kind = ?")
            params.append(kind)
        if project is not None:
            clauses.append("d.project = ?")
            params.append(project)
        sql = (
            "SELECT c.id, c.document_id, c.slug, c.heading_path, c.body, "
            "c.chunk_order, c.content_hash, "
            "d.title, d.kind, d.project, c.summary, c.enrichment, "
            "bm25(documents_fts) AS score "
            "FROM documents_fts "
            "JOIN chunks c ON c.id = documents_fts.chunk_id "
            "JOIN documents d ON d.id = c.document_id "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY rank "
            "LIMIT ?"
        )
        params.append(limit * 2)
        rows = self._conn.execute(sql, params).fetchall()

        ids = extract_identifiers(text)
        if not boost or ids.is_empty() or not rows:
            ranked = sorted(rows, key=lambda r: cast(float, r[12]))[:limit]
            return [
                self._row_to_chunk(row, score=cast(float, row[12]), score_boost=0.0)
                for row in ranked
            ]

        chunk_ids = [cast(str, row[0]) for row in rows]
        kind_hits: dict[str, set[str]] = {cid: set() for cid in chunk_ids}
        self._collect_mention_hits(chunk_ids, ids, kind_hits)
        self._collect_substring_hits(chunk_ids, ids, kind_hits)

        scored: list[tuple[tuple[object, ...], float, float]] = []
        for row in rows:
            cid = cast(str, row[0])
            base = cast(float, row[12])
            boost_value = _BOOST_PER_KIND * len(kind_hits[cid])
            scored.append((row, base, boost_value))
        scored.sort(key=lambda item: item[1] - item[2])
        top = scored[:limit]
        return [
            self._row_to_chunk(row, score=base, score_boost=boost_value)
            for row, base, boost_value in top
        ]

    def _collect_mention_hits(
        self,
        chunk_ids: Sequence[str],
        ids: QueryIdentifiers,
        kind_hits: dict[str, set[str]],
    ) -> None:
        if not chunk_ids:
            return
        chunk_placeholders = ",".join("?" for _ in chunk_ids)
        for target_kind, targets in (("schema", ids.schemas), ("endpoint", ids.endpoints)):
            if not targets:
                continue
            target_placeholders = ",".join("?" for _ in targets)
            sql = (
                f"SELECT DISTINCT chunk_id FROM mentions "
                f"WHERE chunk_id IN ({chunk_placeholders}) "
                f"AND target_kind = ? "
                f"AND target IN ({target_placeholders})"
            )
            params: list[object] = [*chunk_ids, target_kind, *targets]
            for row in self._conn.execute(sql, params):
                kind_hits[cast(str, row[0])].add(target_kind)

    def _collect_substring_hits(
        self,
        chunk_ids: Sequence[str],
        ids: QueryIdentifiers,
        kind_hits: dict[str, set[str]],
    ) -> None:
        if not chunk_ids:
            return
        label_targets: list[tuple[str, tuple[str, ...]]] = [
            ("jira", ids.jira),
            ("release", ids.release),
            ("acceptance", ids.acceptance),
        ]
        if not any(targets for _, targets in label_targets):
            return
        chunk_placeholders = ",".join("?" for _ in chunk_ids)
        sql = f"SELECT id, body FROM chunks WHERE id IN ({chunk_placeholders})"
        rows = self._conn.execute(sql, list(chunk_ids)).fetchall()
        for label, targets in label_targets:
            if not targets:
                continue
            patterns = [re.compile(r"\b" + re.escape(t) + r"\b") for t in targets]
            for row in rows:
                cid = cast(str, row[0])
                body = cast(str, row[1])
                if any(p.search(body) for p in patterns):
                    kind_hits[cid].add(label)

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

    def upsert_mentions(self, chunk_id: str, mentions: Sequence[Mention]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM mentions WHERE chunk_id = ?", (chunk_id,))
            if not mentions:
                return
            self._conn.executemany(
                """
                INSERT INTO mentions (chunk_id, target, target_kind, line_range)
                VALUES (?, ?, ?, ?)
                """,
                [(chunk_id, m.target, m.target_kind, m.line_range) for m in mentions],
            )

    def get_mentions(self, chunk_id: str) -> _MentionList:
        rows = self._conn.execute(
            """
            SELECT target, target_kind, line_range
            FROM mentions
            WHERE chunk_id = ?
            ORDER BY target_kind, target, COALESCE(line_range, '')
            """,
            (chunk_id,),
        ).fetchall()
        return [
            Mention(
                target=cast(str, row[0]),
                target_kind=cast(MentionKind, row[1]),
                line_range=cast("str | None", row[2]),
            )
            for row in rows
        ]

    def find_backlinks(self, target: str, *, target_kind: str | None = None) -> _ChunkList:
        clauses: list[str] = ["m.target = ?"]
        params: list[object] = [target]
        if target_kind is not None:
            clauses.append("m.target_kind = ?")
            params.append(target_kind)
        sql = (
            "SELECT DISTINCT c.id, c.document_id, c.slug, c.heading_path, c.body, "
            "c.chunk_order, c.content_hash, "
            "d.title, d.kind, d.project, c.summary, c.enrichment "
            "FROM mentions m "
            "JOIN chunks c ON c.id = m.chunk_id "
            "JOIN documents d ON d.id = c.document_id "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY c.id"
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def upsert_chunk_enrichment(self, chunk_id: str, enrichment_text: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE chunks SET enrichment = ? WHERE id = ?",
                (enrichment_text, chunk_id),
            )
            self._conn.execute(
                "UPDATE documents_fts SET enrichment = ? WHERE chunk_id = ?",
                (enrichment_text, chunk_id),
            )

    def enrichment_cache_get(self, content_hash: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT enrichment_json, model, generated_at FROM enrichment_cache "
            "WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if row is None:
            return None
        return {
            "enrichment_json": cast(str, row[0]),
            "model": cast(str, row[1]),
            "generated_at": cast(str, row[2]),
        }

    def enrichment_cache_put(
        self,
        *,
        content_hash: str,
        enrichment_json: str,
        model: str,
        generated_at: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO enrichment_cache "
                "(content_hash, enrichment_json, model, generated_at) "
                "VALUES (?, ?, ?, ?)",
                (content_hash, enrichment_json, model, generated_at),
            )

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

    def _row_to_chunk(
        self,
        row: tuple[object, ...],
        *,
        score: float = 0.0,
        score_boost: float = 0.0,
    ) -> StoredChunk:
        heading_path_raw = cast(str, row[3])
        heading_path = tuple(cast(list[str], json.loads(heading_path_raw)))
        return StoredChunk(
            id=cast(str, row[0]),
            document_id=cast(str, row[1]),
            slug=cast(str, row[2]),
            heading_path=heading_path,
            body=cast(str, row[4]),
            order=cast(int, row[5]),
            content_hash=cast(str, row[6]),
            document_title=cast(str, row[7]),
            document_kind=cast(str, row[8]),
            document_project=cast("str | None", row[9]),
            summary=cast("str | None", row[10]),
            enrichment=cast("str | None", row[11]),
            score=score,
            score_boost=score_boost,
        )
