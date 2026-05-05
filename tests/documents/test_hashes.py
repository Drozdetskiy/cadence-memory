"""Unit tests for content_hash, frontmatter_hash, and annotation_hash."""

from __future__ import annotations

import datetime as dt
import hashlib

from cadence_memory.documents.hashes import (
    annotation_hash,
    content_hash,
    frontmatter_hash,
)

SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SHA256_X = "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"


def test_content_hash_empty_string_known_vector() -> None:
    digest = content_hash("")
    assert digest == SHA256_EMPTY
    assert len(digest) == 64
    assert digest == digest.lower()


def test_content_hash_single_char_known_vector() -> None:
    assert content_hash("x") == SHA256_X


def test_content_hash_normalises_crlf_to_lf() -> None:
    assert content_hash("line1\r\nline2\r\n") == content_hash("line1\nline2\n")


def test_content_hash_normalises_bare_cr_to_lf() -> None:
    assert content_hash("line1\rline2\r") == content_hash("line1\nline2\n")


def test_content_hash_treats_all_three_endings_equivalently() -> None:
    lf = content_hash("a\nb\nc")
    crlf = content_hash("a\r\nb\r\nc")
    cr = content_hash("a\rb\rc")
    assert lf == crlf == cr


def test_content_hash_differs_for_different_bodies() -> None:
    assert content_hash("hello") != content_hash("world")


def test_content_hash_returns_lowercase_64_char_hex() -> None:
    digest = content_hash("anything")
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)


def test_frontmatter_hash_empty_equals_sha256_of_empty() -> None:
    assert frontmatter_hash("") == SHA256_EMPTY


def test_frontmatter_hash_matches_raw_sha256_bytes() -> None:
    block = "---\nkind: code-map\ntitle: Billing\n---\n"
    expected = hashlib.sha256(block.encode("utf-8")).hexdigest()
    assert frontmatter_hash(block) == expected


def test_frontmatter_hash_is_sensitive_to_whitespace() -> None:
    a = "---\nkind: code-map\n---\n"
    b = "---\nkind:  code-map\n---\n"
    assert frontmatter_hash(a) != frontmatter_hash(b)


def test_frontmatter_hash_returns_lowercase_64_char_hex() -> None:
    digest = frontmatter_hash("---\nfoo: 1\n---\n")
    assert len(digest) == 64
    assert digest == digest.lower()


def test_annotation_hash_independent_of_insertion_order() -> None:
    a: dict[str, object] = {}
    a["title"] = "Billing"
    a["kind"] = "code-map"
    a["tags"] = ["billing", "stripe"]

    b: dict[str, object] = {}
    b["tags"] = ["billing", "stripe"]
    b["kind"] = "code-map"
    b["title"] = "Billing"

    assert annotation_hash(a) == annotation_hash(b)


def test_annotation_hash_changes_when_value_changes() -> None:
    base = {"kind": "code-map", "title": "Billing"}
    changed = {"kind": "code-map", "title": "Payments"}
    assert annotation_hash(base) != annotation_hash(changed)


def test_annotation_hash_changes_when_list_order_changes() -> None:
    a = {"tags": ["a", "b"]}
    b = {"tags": ["b", "a"]}
    assert annotation_hash(a) != annotation_hash(b)


def test_annotation_hash_handles_date_via_default_str() -> None:
    merged = {"last_confirmed_at": dt.date(2026, 5, 5)}
    digest = annotation_hash(merged)
    assert len(digest) == 64
    assert digest == annotation_hash({"last_confirmed_at": "2026-05-05"})


def test_annotation_hash_handles_datetime_via_default_str() -> None:
    merged = {"last_confirmed_at": dt.datetime(2026, 5, 5, 12, 30, 0)}
    digest = annotation_hash(merged)
    assert len(digest) == 64
    assert digest == annotation_hash({"last_confirmed_at": str(dt.datetime(2026, 5, 5, 12, 30, 0))})


def test_annotation_hash_empty_mapping() -> None:
    digest = annotation_hash({})
    expected = hashlib.sha256(b"{}").hexdigest()
    assert digest == expected


def test_annotation_hash_returns_lowercase_64_char_hex() -> None:
    digest = annotation_hash({"k": "v"})
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)
