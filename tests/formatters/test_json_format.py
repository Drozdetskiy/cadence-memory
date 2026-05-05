"""Tests for the JSON formatter."""

from __future__ import annotations

import json

from cadence_memory.formatters import format_document, format_documents
from cadence_memory.store.interface import StoredDocument


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
