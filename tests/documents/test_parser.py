"""Unit tests for the markdown frontmatter parser."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

from cadence_memory.documents.hashes import frontmatter_hash
from cadence_memory.documents.parser import ParsedDocument, parse_file, parse_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_text_full_frontmatter_returns_expected_fields() -> None:
    text = (FIXTURES / "with_full_frontmatter.md").read_text(encoding="utf-8")
    doc = parse_text(text)

    assert isinstance(doc, ParsedDocument)
    assert doc.frontmatter == {
        "kind": "code-map",
        "title": "Billing",
        "tags": ["billing", "stripe"],
    }
    assert doc.frontmatter_text.startswith("---\n")
    assert doc.frontmatter_text.endswith("---\n")
    assert "kind: code-map" in doc.frontmatter_text
    assert not doc.body.startswith("---")
    assert "# Billing" in doc.body
    assert doc.h1_title == "Billing"


def test_frontmatter_text_round_trips_through_frontmatter_hash() -> None:
    text = (FIXTURES / "with_full_frontmatter.md").read_text(encoding="utf-8")
    doc = parse_text(text)

    expected = hashlib.sha256(doc.frontmatter_text.encode("utf-8")).hexdigest()
    assert frontmatter_hash(doc.frontmatter_text) == expected
    assert text.startswith(doc.frontmatter_text)


def test_parse_text_no_frontmatter_yields_empty_block_and_full_body() -> None:
    text = (FIXTURES / "no_frontmatter.md").read_text(encoding="utf-8")
    doc = parse_text(text)

    assert doc.frontmatter == {}
    assert doc.frontmatter_text == ""
    assert doc.body == text
    assert doc.h1_title == "Just a Title"


def test_parse_text_frontmatter_without_h1_returns_none_title() -> None:
    text = (FIXTURES / "frontmatter_no_h1.md").read_text(encoding="utf-8")
    doc = parse_text(text)

    assert doc.frontmatter["kind"] == "note"
    assert doc.h1_title is None


def test_parse_file_crlf_endings_finds_h1_and_keeps_multiple_lines() -> None:
    doc = parse_file(FIXTURES / "crlf_line_endings.md")

    assert doc.frontmatter == {"kind": "code-map", "title": "CRLF Doc"}
    assert doc.h1_title == "CRLF Title"
    assert len(doc.body.splitlines()) >= 3
    assert "Body line one." in doc.body
    assert "Body line two." in doc.body


def test_parse_text_with_crlf_string_keeps_crlf_in_frontmatter_text() -> None:
    raw_bytes = (FIXTURES / "crlf_line_endings.md").read_bytes()
    text = raw_bytes.decode("utf-8")
    doc = parse_text(text)

    assert doc.frontmatter_text.startswith("---\r\n")
    assert doc.frontmatter_text.endswith("---\r\n")
    assert doc.h1_title == "CRLF Title"


def test_parse_file_bad_yaml_raises_value_error_with_path() -> None:
    path = FIXTURES / "bad_yaml.md"
    with pytest.raises(ValueError) as excinfo:
        parse_file(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "invalid YAML frontmatter" in message


def test_parse_text_bad_yaml_raises_value_error_without_path() -> None:
    text = (FIXTURES / "bad_yaml.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        parse_text(text)

    message = str(excinfo.value)
    assert "invalid YAML frontmatter" in message
    assert str(FIXTURES / "bad_yaml.md") not in message


def test_parse_file_non_ascii_reads_utf8() -> None:
    path = FIXTURES / "non_ascii.md"
    doc = parse_file(path)

    assert doc.frontmatter["title"] == "Café 日本語"
    assert "Café 日本語" in doc.body
    assert "日本語のテキスト" in doc.body
    assert "ümlaut" in doc.body
    assert "Ω" in doc.body
    assert doc.h1_title == "Café 日本語"


def test_h1_strips_trailing_whitespace_and_ignores_h2_h3() -> None:
    text = (
        "---\n"
        "kind: note\n"
        "---\n"
        "## not an h1\n"
        "### also not\n"
        "#nospace either\n"
        "# Real Title   \n"
        "\n"
        "body\n"
    )
    doc = parse_text(text)

    assert doc.h1_title == "Real Title"


def test_parsed_document_is_frozen() -> None:
    doc = parse_text("# Title\n")
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.body = "mutated"  # type: ignore[misc]


def test_parse_file_returns_parsed_document(tmp_path: Path) -> None:
    src = tmp_path / "doc.md"
    src.write_text("---\nkind: note\n---\n# Hello\n", encoding="utf-8")
    doc = parse_file(src)

    assert doc.frontmatter == {"kind": "note"}
    assert doc.h1_title == "Hello"
    assert doc.frontmatter_text == "---\nkind: note\n---\n"


def test_parse_text_frontmatter_without_trailing_newline_is_recovered() -> None:
    text = "---\nkind: note\ntitle: Tail\n---"
    doc = parse_text(text)

    assert doc.frontmatter == {"kind": "note", "title": "Tail"}
    assert doc.frontmatter_text == text
    assert doc.body == ""
    assert text.startswith(doc.frontmatter_text)


def test_parse_text_preserves_trailing_newline_in_body() -> None:
    text = "---\nkind: note\n---\nbody\n"
    doc = parse_text(text)

    assert doc.body == "body\n"
    assert doc.frontmatter_text + doc.body == text


def test_parse_text_no_frontmatter_preserves_trailing_newlines() -> None:
    text = "# Title\nbody\n\n"
    doc = parse_text(text)

    assert doc.frontmatter_text == ""
    assert doc.body == text


def test_parse_text_leading_whitespace_before_fence_is_rejected() -> None:
    text = "  ---\nkind: note\n---\nbody\n"
    with pytest.raises(ValueError) as excinfo:
        parse_text(text)

    assert "opening '---' fence" in str(excinfo.value)
