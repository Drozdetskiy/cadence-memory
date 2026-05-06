"""JSON output formatter for `--format json`."""

from __future__ import annotations

import json
from typing import Any

from cadence_memory.store.interface import StoredChunk, StoredDocument

__all__ = [
    "format_chunk",
    "format_chunks",
    "format_document",
    "format_documents",
]


_SNIPPET_MAX = 200


def _doc_to_dict(doc: StoredDocument, *, include_body: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": doc.id,
        "source_type": doc.source_type,
        "project": doc.project,
        "abs_path": doc.abs_path,
        "rel_path": doc.rel_path,
        "kind": doc.kind,
        "title": doc.title,
        "tags": list(doc.tags),
        "related": list(doc.related),
        "content_hash": doc.content_hash,
        "frontmatter_hash": doc.frontmatter_hash,
        "annotation_hash": doc.annotation_hash,
        "mtime": doc.mtime,
        "indexed_at": doc.indexed_at,
    }
    if include_body:
        payload["body"] = doc.body
    return payload


def _chunk_snippet(body: str) -> str:
    flat = body.replace("\n", " ").strip()
    if len(flat) <= _SNIPPET_MAX:
        return flat
    return flat[:_SNIPPET_MAX]


def _chunk_to_dict(chunk: StoredChunk) -> dict[str, Any]:
    summary = chunk.summary if chunk.summary is not None else _chunk_snippet(chunk.body)
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "kind": chunk.document_kind,
        "title": chunk.document_title,
        "project": chunk.document_project,
        "heading_path": list(chunk.heading_path),
        "slug": chunk.slug,
        "summary": summary,
        "snippet": _chunk_snippet(chunk.body),
    }


def format_documents(docs: list[StoredDocument], *, include_body: bool) -> str:
    payload = [_doc_to_dict(doc, include_body=include_body) for doc in docs]
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)


def format_document(doc: StoredDocument, *, include_body: bool) -> str:
    payload = _doc_to_dict(doc, include_body=include_body)
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)


def format_chunks(chunks: list[StoredChunk]) -> str:
    payload = [_chunk_to_dict(chunk) for chunk in chunks]
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)


def format_chunk(chunk: StoredChunk) -> str:
    payload = _chunk_to_dict(chunk)
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)
