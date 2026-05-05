"""Unit tests for document ID build/parse/validate."""

from __future__ import annotations

import dataclasses

import pytest

from cadence_memory.documents.ids import (
    DocumentId,
    build_ephemeral_id,
    build_global_id,
    build_project_id,
    parse,
    validate,
)


def test_build_project_id_round_trip() -> None:
    raw = build_project_id("billing", "README.md")
    assert raw == "billing:README.md"
    assert parse(raw) == DocumentId(source_type="project", project="billing", rel_path="README.md")


def test_build_project_id_with_nested_path() -> None:
    raw = build_project_id("billing", "docs/architecture/overview.md")
    assert raw == "billing:docs/architecture/overview.md"
    parsed = parse(raw)
    assert parsed.source_type == "project"
    assert parsed.project == "billing"
    assert parsed.rel_path == "docs/architecture/overview.md"


def test_build_global_id_round_trip() -> None:
    raw = build_global_id("workflows/deploy.md")
    assert raw == ":workflows/deploy.md"
    assert parse(raw) == DocumentId(
        source_type="global", project=None, rel_path="workflows/deploy.md"
    )


def test_build_ephemeral_id_round_trip() -> None:
    raw = build_ephemeral_id("JIRA-1234")
    assert raw == "eph:JIRA-1234"
    assert parse(raw) == DocumentId(source_type="ephemeral", project=None, rel_path=None)


def test_validate_accepts_valid_ids() -> None:
    validate("billing:README.md")
    validate(":workflows/deploy.md")
    validate("eph:JIRA-1234")


@pytest.mark.parametrize(
    "rel_path",
    [
        "/etc/passwd",
        "/leading-slash.md",
    ],
)
def test_rel_path_rejects_leading_slash(rel_path: str) -> None:
    with pytest.raises(ValueError, match="must not start with '/'"):
        build_project_id("p", rel_path)
    with pytest.raises(ValueError, match="must not start with '/'"):
        build_global_id(rel_path)


def test_rel_path_rejects_empty() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        build_project_id("p", "")
    with pytest.raises(ValueError, match="must be non-empty"):
        build_global_id("")


@pytest.mark.parametrize(
    "rel_path",
    [
        "../escape.md",
        "docs/../escape.md",
        "..",
    ],
)
def test_rel_path_rejects_dot_dot_segments(rel_path: str) -> None:
    with pytest.raises(ValueError, match=r"'\.\.' path segment"):
        build_project_id("p", rel_path)


def test_rel_path_rejects_backslash() -> None:
    with pytest.raises(ValueError, match="backslash"):
        build_project_id("p", "docs\\windows.md")


def test_rel_path_rejects_colon() -> None:
    with pytest.raises(ValueError, match="must not contain ':'"):
        build_project_id("p", "docs/has:colon.md")


def test_project_name_rejects_empty() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        build_project_id("", "README.md")


def test_project_name_rejects_colon() -> None:
    with pytest.raises(ValueError, match="must not contain ':'"):
        build_project_id("bad:name", "README.md")


def test_project_name_rejects_slash() -> None:
    with pytest.raises(ValueError, match="must not contain '/'"):
        build_project_id("bad/name", "README.md")


def test_project_name_rejects_reserved_eph_prefix() -> None:
    with pytest.raises(ValueError, match="reserved"):
        build_project_id("eph", "README.md")


def test_ephemeral_name_rejects_empty() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        build_ephemeral_id("")


def test_ephemeral_name_rejects_colon() -> None:
    with pytest.raises(ValueError, match="must not contain ':'"):
        build_ephemeral_id("bad:name")


def test_ephemeral_name_rejects_slash() -> None:
    with pytest.raises(ValueError, match="must not contain '/'"):
        build_ephemeral_id("bad/name")


def test_parse_rejects_double_colon_in_project_id() -> None:
    with pytest.raises(ValueError, match="must not contain ':'"):
        parse("project::path.md")


def test_parse_rejects_id_without_colon() -> None:
    with pytest.raises(ValueError, match="missing ':'"):
        parse("no-colon-here")


def test_parse_rejects_empty_path_in_project_id() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        parse("project:")


def test_parse_rejects_empty_ephemeral_name() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        parse("eph:")


def test_parse_rejects_empty_global_path() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        parse(":")


def test_validate_propagates_value_error() -> None:
    with pytest.raises(ValueError):
        validate("project::path.md")


def test_document_id_is_frozen() -> None:
    doc = DocumentId(source_type="project", project="p", rel_path="r.md")
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.project = "other"  # type: ignore[misc]


def test_document_id_equality_and_hash() -> None:
    a = DocumentId(source_type="project", project="p", rel_path="r.md")
    b = DocumentId(source_type="project", project="p", rel_path="r.md")
    c = DocumentId(source_type="global", project=None, rel_path="r.md")
    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    assert {a, b, c} == {a, c}
