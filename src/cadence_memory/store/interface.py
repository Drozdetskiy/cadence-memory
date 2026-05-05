"""Store Protocol and StoredDocument dataclass defining the storage boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

__all__ = ["Store", "StoredDocument"]


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


type _DocList = list[StoredDocument]


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

    def query(
        self,
        text: str,
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> _DocList: ...

    def all_ids(self) -> set[str]: ...

    def close(self) -> None: ...
