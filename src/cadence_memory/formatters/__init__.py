"""Output formatters for `--format json` and `--format table`."""

from __future__ import annotations

from typing import Literal

from cadence_memory.formatters import json_format, table_format
from cadence_memory.store.interface import StoredChunk, StoredDocument

__all__ = [
    "Format",
    "format_chunk",
    "format_chunks",
    "format_document",
    "format_documents",
]

type Format = Literal["json", "table"]


def format_documents(
    docs: list[StoredDocument], *, format: Format, include_body: bool = False
) -> str:
    if format == "json":
        return json_format.format_documents(docs, include_body=include_body)
    return table_format.format_documents(docs)


def format_document(doc: StoredDocument, *, format: Format, include_body: bool = True) -> str:
    if format == "json":
        return json_format.format_document(doc, include_body=include_body)
    return table_format.format_document(doc, include_body=include_body)


def format_chunks(chunks: list[StoredChunk], *, format: Format) -> str:
    if format == "json":
        return json_format.format_chunks(chunks)
    return table_format.format_chunks(chunks)


def format_chunk(chunk: StoredChunk, *, format: Format) -> str:
    if format == "json":
        return json_format.format_chunk(chunk)
    return table_format.format_chunk(chunk)
