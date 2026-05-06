"""Reranker Protocol and rerank dataclasses."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = ["RerankItem", "RerankedItem", "Reranker"]


@dataclass(frozen=True, slots=True)
class RerankItem:
    chunk_id: str
    title: str
    heading_path: tuple[str, ...]
    summary: str | None
    body_excerpt: str


@dataclass(frozen=True, slots=True)
class RerankedItem:
    chunk_id: str
    score: float
    rank: int


class Reranker(Protocol):
    model: str

    def rerank(
        self,
        query: str,
        items: Sequence[RerankItem],
    ) -> list[RerankedItem]: ...
