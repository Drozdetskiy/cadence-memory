"""Store Protocol and StoredDocument/StoredChunk dataclasses defining the storage boundary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from cadence_memory.documents.chunker import Chunk

__all__ = ["Store", "StoredChunk", "StoredDocument"]


@dataclass(frozen=True, slots=True)
class StoredDocument:
    id: str
    source_type: Literal["project", "global", "ephemeral"]
    project: str | None
    abs_path: str
    rel_path: str
    kind: str
    title: str
    body: str
    tags: tuple[str, ...]
    related: tuple[str, ...]
    content_hash: str
    frontmatter_hash: str
    annotation_hash: str
    mtime: int
    indexed_at: str


@dataclass(frozen=True, slots=True)
class StoredChunk:
    id: str
    document_id: str
    slug: str
    heading_path: tuple[str, ...]
    body: str
    order: int
    content_hash: str
    document_title: str
    document_kind: str
    document_project: str | None
    summary: str | None = None
    enrichment: str | None = None


type _DocList = list[StoredDocument]
type _ChunkList = list[StoredChunk]


class Store(Protocol):
    def upsert(self, doc: StoredDocument) -> None: ...

    def delete(self, doc_id: str) -> None: ...

    def get(self, doc_id: str) -> StoredDocument | None: ...

    def list(
        self,
        *,
        kind: str | None = None,
        project: str | None = None,
        source_type: str | None = None,
    ) -> _DocList: ...

    def upsert_chunks(self, document_id: str, chunks: Sequence[Chunk]) -> None: ...

    def get_chunks(self, document_id: str) -> _ChunkList: ...

    def get_chunk(self, chunk_id: str) -> StoredChunk | None: ...

    def query(
        self,
        text: str,
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> _ChunkList: ...

    def all_ids(self) -> set[str]: ...

    def discover_cache_get(self, *, path: str, content_hash: str) -> dict[str, object] | None: ...

    def discover_cache_put(
        self,
        *,
        path: str,
        content_hash: str,
        annotation_json: str,
        model: str,
    ) -> None: ...

    def discover_cache_clear(self) -> None: ...

    def upsert_chunk_enrichment(self, chunk_id: str, enrichment_text: str) -> None: ...

    def enrichment_cache_get(self, content_hash: str) -> dict[str, object] | None: ...

    def enrichment_cache_put(
        self,
        *,
        content_hash: str,
        enrichment_json: str,
        model: str,
        generated_at: str,
    ) -> None: ...

    def close(self) -> None: ...
