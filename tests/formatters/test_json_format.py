"""Tests for the JSON formatter."""

from __future__ import annotations

import json

from cadence_memory.formatters import (
    format_chunk,
    format_chunks,
    format_document,
    format_documents,
)
from cadence_memory.store.interface import StoredChunk, StoredDocument


def _make_doc(
    *,
    doc_id: str = "proj:README.md",
    title: str = "Title",
    body: str = "body text",
    tags: tuple[str, ...] = ("alpha", "beta"),
    related: tuple[str, ...] = ("proj:other.md",),
) -> StoredDocument:
    return StoredDocument(
        id=doc_id,
        source_type="project",
        project="proj",
        abs_path="/abs/proj/README.md",
        rel_path="README.md",
        kind="doc",
        title=title,
        body=body,
        tags=tags,
        related=related,
        content_hash="c" * 64,
        frontmatter_hash="f" * 64,
        annotation_hash="a" * 64,
        mtime=1234567890,
        indexed_at="2026-05-05T00:00:00Z",
    )


def test_format_documents_returns_json_array() -> None:
    docs = [_make_doc(doc_id="proj:a.md"), _make_doc(doc_id="proj:b.md")]

    output = format_documents(docs, format="json", include_body=False)
    parsed = json.loads(output)

    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert {entry["id"] for entry in parsed} == {"proj:a.md", "proj:b.md"}


def test_format_documents_omits_body_by_default() -> None:
    docs = [_make_doc(body="secret body")]

    output = format_documents(docs, format="json", include_body=False)
    parsed = json.loads(output)

    assert "body" not in parsed[0]
    assert "secret body" not in output


def test_format_documents_includes_body_when_requested() -> None:
    docs = [_make_doc(body="visible body")]

    output = format_documents(docs, format="json", include_body=True)
    parsed = json.loads(output)

    assert parsed[0]["body"] == "visible body"


def test_format_documents_empty_list_is_empty_json_array() -> None:
    output = format_documents([], format="json", include_body=False)

    assert json.loads(output) == []


def test_format_document_returns_json_object() -> None:
    doc = _make_doc()

    output = format_document(doc, format="json", include_body=False)
    parsed = json.loads(output)

    assert isinstance(parsed, dict)
    assert parsed["id"] == doc.id
    assert parsed["kind"] == doc.kind
    assert parsed["tags"] == list(doc.tags)
    assert parsed["related"] == list(doc.related)
    assert "body" not in parsed


def test_format_document_includes_body_when_requested() -> None:
    doc = _make_doc(body="full body\nwith newline")

    output = format_document(doc, format="json", include_body=True)
    parsed = json.loads(output)

    assert parsed["body"] == "full body\nwith newline"


def test_format_document_passes_unicode_through() -> None:
    doc = _make_doc(title="Héllo 🌍", body="café")

    output = format_document(doc, format="json", include_body=True)

    assert "Héllo 🌍" in output
    assert "café" in output
    parsed = json.loads(output)
    assert parsed["title"] == "Héllo 🌍"
    assert parsed["body"] == "café"


def _make_chunk(
    *,
    chunk_id: str = "proj:README.md#overview",
    document_id: str = "proj:README.md",
    slug: str = "overview",
    heading_path: tuple[str, ...] = ("Overview",),
    body: str = "## Overview\n\nchunk body text\n",
    order: int = 1,
    document_title: str = "Title",
    document_kind: str = "doc",
    document_project: str | None = "proj",
    summary: str | None = None,
    score: float = 0.0,
    score_boost: float = 0.0,
    score_rerank: float | None = None,
) -> StoredChunk:
    return StoredChunk(
        id=chunk_id,
        document_id=document_id,
        slug=slug,
        heading_path=heading_path,
        body=body,
        order=order,
        content_hash="c" * 64,
        document_title=document_title,
        document_kind=document_kind,
        document_project=document_project,
        summary=summary,
        score=score,
        score_boost=score_boost,
        score_rerank=score_rerank,
    )


def test_format_chunks_returns_json_array() -> None:
    chunks = [
        _make_chunk(chunk_id="proj:a.md#one", document_id="proj:a.md"),
        _make_chunk(chunk_id="proj:a.md#two", document_id="proj:a.md", slug="two"),
    ]

    output = format_chunks(chunks, format="json")
    parsed = json.loads(output)

    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert {entry["chunk_id"] for entry in parsed} == {"proj:a.md#one", "proj:a.md#two"}


def test_format_chunks_includes_expected_fields() -> None:
    chunks = [_make_chunk()]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert set(parsed[0].keys()) == {
        "chunk_id",
        "document_id",
        "kind",
        "title",
        "project",
        "heading_path",
        "slug",
        "summary",
        "snippet",
        "score",
        "score_boost",
    }
    assert parsed[0]["heading_path"] == ["Overview"]
    assert parsed[0]["kind"] == "doc"
    assert parsed[0]["title"] == "Title"
    assert parsed[0]["project"] == "proj"
    assert parsed[0]["slug"] == "overview"
    assert parsed[0]["score"] == 0.0
    assert parsed[0]["score_boost"] == 0.0


def test_format_chunks_snippet_truncated_to_200_chars() -> None:
    body = "X" * 500
    chunks = [_make_chunk(body=body)]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert len(parsed[0]["snippet"]) == 200
    assert parsed[0]["snippet"] == "X" * 200


def test_format_chunks_snippet_collapses_newlines() -> None:
    chunks = [_make_chunk(body="\nline one\nline two\n")]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert parsed[0]["snippet"] == "line one line two"


def test_format_chunks_empty_list_is_empty_json_array() -> None:
    output = format_chunks([], format="json")

    assert json.loads(output) == []


def test_format_chunks_preamble_has_empty_heading_path() -> None:
    chunks = [_make_chunk(heading_path=(), slug="_preamble", body="leading body")]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert parsed[0]["heading_path"] == []
    assert parsed[0]["slug"] == "_preamble"


def test_format_chunk_returns_json_object() -> None:
    chunk = _make_chunk()

    output = format_chunk(chunk, format="json")
    parsed = json.loads(output)

    assert isinstance(parsed, dict)
    assert parsed["chunk_id"] == chunk.id


def test_format_chunks_surfaces_summary_when_present() -> None:
    chunks = [_make_chunk(summary="a meaningful summary phrase")]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert parsed[0]["summary"] == "a meaningful summary phrase"
    assert "snippet" in parsed[0]


def test_format_chunks_summary_falls_back_to_body_slice_when_none() -> None:
    body = "X" * 500
    chunks = [_make_chunk(body=body, summary=None)]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert parsed[0]["summary"] is not None
    assert parsed[0]["summary"] == "X" * 200
    assert parsed[0]["snippet"] == "X" * 200


def test_format_chunk_includes_summary_key() -> None:
    chunk = _make_chunk(summary="explicit summary text")

    parsed = json.loads(format_chunk(chunk, format="json"))

    assert parsed["summary"] == "explicit summary text"


def test_format_chunks_surfaces_score_and_boost() -> None:
    chunks = [_make_chunk(score=-3.5, score_boost=10.0)]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert parsed[0]["score"] == -3.5
    assert parsed[0]["score_boost"] == 10.0


def test_format_chunk_surfaces_score_and_boost() -> None:
    chunk = _make_chunk(score=-1.25, score_boost=5.0)

    parsed = json.loads(format_chunk(chunk, format="json"))

    assert parsed["score"] == -1.25
    assert parsed["score_boost"] == 5.0


def test_format_chunks_omits_score_rerank_when_none() -> None:
    chunks = [_make_chunk(score_rerank=None)]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert "score_rerank" not in parsed[0]


def test_format_chunks_includes_score_rerank_when_set() -> None:
    chunks = [_make_chunk(score_rerank=8.7)]

    parsed = json.loads(format_chunks(chunks, format="json"))

    assert parsed[0]["score_rerank"] == 8.7


def test_format_chunk_omits_score_rerank_when_none() -> None:
    chunk = _make_chunk(score_rerank=None)

    parsed = json.loads(format_chunk(chunk, format="json"))

    assert "score_rerank" not in parsed


def test_format_chunk_includes_score_rerank_when_set() -> None:
    chunk = _make_chunk(score_rerank=3.25)

    parsed = json.loads(format_chunk(chunk, format="json"))

    assert parsed["score_rerank"] == 3.25


def test_format_documents_preserves_all_metadata_fields() -> None:
    doc = _make_doc()
    expected_fields = {
        "id",
        "source_type",
        "project",
        "abs_path",
        "rel_path",
        "kind",
        "title",
        "tags",
        "related",
        "content_hash",
        "frontmatter_hash",
        "annotation_hash",
        "mtime",
        "indexed_at",
    }

    parsed = json.loads(format_documents([doc], format="json", include_body=False))

    assert set(parsed[0].keys()) == expected_fields
