"""Tests for the table formatter."""

from __future__ import annotations

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
    kind: str = "doc",
    project: str | None = "proj",
    title: str = "Title",
    body: str = "body text",
    tags: tuple[str, ...] = ("alpha", "beta"),
    related: tuple[str, ...] = (),
) -> StoredDocument:
    return StoredDocument(
        id=doc_id,
        source_type="project",
        project=project,
        abs_path="/abs/path",
        rel_path="README.md",
        kind=kind,
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


def test_format_documents_renders_column_headers() -> None:
    docs = [_make_doc()]

    output = format_documents(docs, format="table")
    header_line = output.splitlines()[0]

    for column in ("id", "kind", "project", "title", "tags"):
        assert column in header_line


def test_format_documents_truncates_long_titles() -> None:
    long_title = "A" * 80
    docs = [_make_doc(title=long_title)]

    output = format_documents(docs, format="table")

    assert "…" in output
    assert "A" * 80 not in output


def test_format_documents_keeps_short_titles_intact() -> None:
    docs = [_make_doc(title="Short")]

    output = format_documents(docs, format="table")

    assert "Short" in output
    assert "…" not in output


def test_format_documents_empty_list() -> None:
    output = format_documents([], format="table")

    assert output == "(no documents)"


def test_format_documents_joins_tags_with_comma_space() -> None:
    docs = [_make_doc(tags=("first", "second"))]

    output = format_documents(docs, format="table")

    assert "first, second" in output


def test_format_documents_renders_one_line_per_document() -> None:
    docs = [
        _make_doc(doc_id="proj:a.md"),
        _make_doc(doc_id="proj:b.md"),
        _make_doc(doc_id="proj:c.md"),
    ]

    output = format_documents(docs, format="table")
    lines = output.splitlines()

    assert len(lines) == 4  # header + 3 docs
    assert "proj:a.md" in lines[1]
    assert "proj:b.md" in lines[2]
    assert "proj:c.md" in lines[3]


def test_format_document_contains_body_after_separator() -> None:
    doc = _make_doc(body="the body content here")

    output = format_document(doc, format="table", include_body=True)

    assert "---" in output
    sep_index = output.index("---")
    assert "the body content here" in output[sep_index:]


def test_format_document_contains_metadata_fields() -> None:
    doc = _make_doc()

    output = format_document(doc, format="table", include_body=True)

    for label in (
        "id",
        "kind",
        "project",
        "title",
        "tags",
        "related",
        "content_hash",
        "frontmatter_hash",
        "annotation_hash",
        "indexed_at",
    ):
        assert label in output


def test_format_document_omits_body_when_include_body_false() -> None:
    doc = _make_doc(body="should-not-appear")

    output = format_document(doc, format="table", include_body=False)

    assert "should-not-appear" not in output
    assert "---" not in output


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
    )


def test_format_chunks_renders_column_headers() -> None:
    chunks = [_make_chunk()]

    output = format_chunks(chunks, format="table")
    header_line = output.splitlines()[0]

    for column in ("kind", "chunk_id", "heading"):
        assert column in header_line


def test_format_chunks_empty_list() -> None:
    output = format_chunks([], format="table")

    assert output == "(no chunks)"


def test_format_chunks_preamble_has_empty_heading() -> None:
    chunks = [
        _make_chunk(
            chunk_id="proj:README.md#_preamble",
            slug="_preamble",
            heading_path=(),
        )
    ]

    output = format_chunks(chunks, format="table")
    lines = output.splitlines()

    assert len(lines) == 2  # header + 1 row
    assert "proj:README.md#_preamble" in lines[1]
    heading_col_index = lines[0].index("heading")
    assert lines[1][heading_col_index:].strip() == ""


def test_format_chunks_renders_one_line_per_chunk() -> None:
    chunks = [
        _make_chunk(chunk_id="proj:a.md#one", document_id="proj:a.md"),
        _make_chunk(chunk_id="proj:a.md#two", document_id="proj:a.md", slug="two"),
        _make_chunk(chunk_id="proj:b.md#one", document_id="proj:b.md"),
    ]

    output = format_chunks(chunks, format="table")
    lines = output.splitlines()

    assert len(lines) == 4  # header + 3 rows
    assert "proj:a.md#one" in lines[1]
    assert "proj:a.md#two" in lines[2]
    assert "proj:b.md#one" in lines[3]


def test_format_chunks_joins_heading_path_with_arrow() -> None:
    chunks = [_make_chunk(heading_path=("Top", "Sub"))]

    output = format_chunks(chunks, format="table")

    assert "Top > Sub" in output


def test_format_chunks_truncates_long_heading() -> None:
    long_heading = "A" * 100
    chunks = [_make_chunk(heading_path=(long_heading,))]

    output = format_chunks(chunks, format="table")

    assert "…" in output
    assert "A" * 100 not in output


def test_format_chunk_contains_body_after_separator() -> None:
    chunk = _make_chunk(body="the chunk body content")

    output = format_chunk(chunk, format="table")

    assert "---" in output
    sep_index = output.index("---")
    assert "the chunk body content" in output[sep_index:]
