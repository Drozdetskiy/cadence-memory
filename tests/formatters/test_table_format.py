"""Tests for the table formatter."""

from __future__ import annotations

from cadence_memory.formatters import format_document, format_documents
from cadence_memory.store.interface import StoredDocument


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
