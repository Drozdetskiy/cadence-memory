"""Unit tests for the annotations merge helper."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from cadence_memory.documents.annotations import MergedAnnotation, merge


def test_frontmatter_only_uses_frontmatter_for_all_fields() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={
            "kind": "service",
            "title": "Billing",
            "tags": ["api", "stripe"],
            "related": ["orders:README.md"],
            "project": "billing",
            "confidence": "curated",
            "last_confirmed_at": "2026-05-01",
        },
        h1_title=None,
        filename_fallback="README.md",
        defaults_kind="doc",
    )

    assert result.kind == "service"
    assert result.title == "Billing"
    assert result.tags == ("api", "stripe")
    assert result.related == ("orders:README.md",)
    assert result.project == "billing"
    assert result.confidence == "curated"
    assert result.last_confirmed_at == "2026-05-01"
    assert result.provenance == {
        "kind": "frontmatter",
        "title": "frontmatter",
        "tags": "frontmatter",
        "related": "frontmatter",
        "project": "frontmatter",
        "confidence": "frontmatter",
        "last_confirmed_at": "frontmatter",
    }


def test_annotations_only_uses_annotations_for_all_fields() -> None:
    result = merge(
        annotations_entry={
            "kind": "doc",
            "title": "Billing Notes",
            "tags": ["stripe"],
            "related": [":workflows/deploy.md"],
            "project": "billing",
            "confidence": "auto",
            "last_confirmed_at": "2026-04-01",
        },
        frontmatter={},
        h1_title=None,
        filename_fallback="README.md",
        defaults_kind="doc",
    )

    assert result.kind == "doc"
    assert result.title == "Billing Notes"
    assert result.tags == ("stripe",)
    assert result.related == (":workflows/deploy.md",)
    assert result.project == "billing"
    assert result.confidence == "auto"
    assert result.last_confirmed_at == "2026-04-01"
    assert result.provenance == {
        "kind": "annotations-config",
        "title": "annotations-config",
        "tags": "annotations-config",
        "related": "annotations-config",
        "project": "annotations-config",
        "confidence": "annotations-config",
        "last_confirmed_at": "annotations-config",
    }


def test_both_sides_present_frontmatter_wins_scalars_lists_merge() -> None:
    result = merge(
        annotations_entry={
            "kind": "doc",
            "title": "From Curator",
            "tags": ["stripe"],
            "related": [":workflows/deploy.md"],
            "project": "billing-curator",
            "confidence": "auto",
            "last_confirmed_at": "2026-04-01",
        },
        frontmatter={
            "kind": "service",
            "title": "From Author",
            "tags": ["api", "public"],
            "related": ["orders:README.md"],
            "project": "billing",
            "confidence": "curated",
            "last_confirmed_at": "2026-05-01",
        },
        h1_title="Should Be Ignored",
        filename_fallback="README.md",
        defaults_kind="doc",
    )

    assert result.kind == "service"
    assert result.title == "From Author"
    assert result.project == "billing"
    assert result.confidence == "curated"
    assert result.last_confirmed_at == "2026-05-01"
    assert result.tags == ("stripe", "api", "public")
    assert result.related == (":workflows/deploy.md", "orders:README.md")
    assert result.provenance == {
        "kind": "frontmatter",
        "title": "frontmatter",
        "tags": "merged",
        "related": "merged",
        "project": "frontmatter",
        "confidence": "frontmatter",
        "last_confirmed_at": "frontmatter",
    }


def test_neither_side_falls_back_to_defaults_h1_and_empty_lists() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={},
        h1_title="My Heading",
        filename_fallback="notes.md",
        defaults_kind="doc",
    )

    assert result.kind == "doc"
    assert result.title == "My Heading"
    assert result.tags == ()
    assert result.related == ()
    assert result.project is None
    assert result.confidence is None
    assert result.last_confirmed_at is None
    assert result.provenance == {
        "kind": "default",
        "title": "default",
        "tags": "default",
        "related": "default",
        "project": "default",
        "confidence": "default",
        "last_confirmed_at": "default",
    }


def test_neither_side_no_h1_falls_back_to_filename() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={},
        h1_title=None,
        filename_fallback="notes.md",
        defaults_kind="doc",
    )

    assert result.title == "notes.md"
    assert result.provenance["title"] == "default"


def test_title_priority_frontmatter_over_annotations_h1_and_filename() -> None:
    result = merge(
        annotations_entry={"title": "From Curator"},
        frontmatter={"title": "From Author"},
        h1_title="From H1",
        filename_fallback="from-file.md",
        defaults_kind="doc",
    )
    assert result.title == "From Author"
    assert result.provenance["title"] == "frontmatter"


def test_title_priority_annotations_over_h1_and_filename() -> None:
    result = merge(
        annotations_entry={"title": "From Curator"},
        frontmatter={},
        h1_title="From H1",
        filename_fallback="from-file.md",
        defaults_kind="doc",
    )
    assert result.title == "From Curator"
    assert result.provenance["title"] == "annotations-config"


def test_title_priority_h1_over_filename() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={},
        h1_title="From H1",
        filename_fallback="from-file.md",
        defaults_kind="doc",
    )
    assert result.title == "From H1"
    assert result.provenance["title"] == "default"


def test_title_priority_filename_when_h1_is_none() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={},
        h1_title=None,
        filename_fallback="from-file.md",
        defaults_kind="doc",
    )
    assert result.title == "from-file.md"
    assert result.provenance["title"] == "default"


def test_tag_merge_dedupes_case_sensitively() -> None:
    result = merge(
        annotations_entry={"tags": ["api"]},
        frontmatter={"tags": ["API", "api"]},
        h1_title=None,
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result.tags == ("api", "API")
    assert result.provenance["tags"] == "merged"


def test_tag_merge_order_annotations_first_then_frontmatter() -> None:
    result = merge(
        annotations_entry={"tags": ["alpha", "beta"]},
        frontmatter={"tags": ["gamma", "alpha"]},
        h1_title=None,
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result.tags == ("alpha", "beta", "gamma")
    assert result.provenance["tags"] == "merged"


def test_related_merge_order_annotations_first_then_frontmatter() -> None:
    result = merge(
        annotations_entry={"related": ["a:one.md", "b:two.md"]},
        frontmatter={"related": ["c:three.md", "a:one.md"]},
        h1_title=None,
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result.related == ("a:one.md", "b:two.md", "c:three.md")
    assert result.provenance["related"] == "merged"


def test_empty_string_kind_in_frontmatter_falls_through_to_annotations() -> None:
    result = merge(
        annotations_entry={"kind": "doc"},
        frontmatter={"kind": ""},
        h1_title=None,
        filename_fallback="x.md",
        defaults_kind="pattern",
    )
    assert result.kind == "doc"
    assert result.provenance["kind"] == "annotations-config"


def test_empty_string_title_in_both_falls_through_to_h1() -> None:
    result = merge(
        annotations_entry={"title": ""},
        frontmatter={"title": ""},
        h1_title="H1 Title",
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result.title == "H1 Title"
    assert result.provenance["title"] == "default"


def test_empty_string_project_falls_through_to_none() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={"project": ""},
        h1_title=None,
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result.project is None
    assert result.provenance["project"] == "default"


def test_only_one_side_contributes_tags_provenance_reflects_that_side() -> None:
    result_fm = merge(
        annotations_entry={"tags": []},
        frontmatter={"tags": ["api"]},
        h1_title=None,
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result_fm.tags == ("api",)
    assert result_fm.provenance["tags"] == "frontmatter"

    result_ann = merge(
        annotations_entry={"tags": ["stripe"]},
        frontmatter={"tags": []},
        h1_title=None,
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result_ann.tags == ("stripe",)
    assert result_ann.provenance["tags"] == "annotations-config"


def test_frontmatter_tags_not_a_list_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        merge(
            annotations_entry=None,
            frontmatter={"tags": "single-string"},
            h1_title=None,
            filename_fallback="x.md",
            defaults_kind="doc",
        )
    assert "tags" in str(excinfo.value)
    assert "frontmatter" in str(excinfo.value)


def test_annotations_tags_not_a_list_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        merge(
            annotations_entry={"tags": "single-string"},
            frontmatter={},
            h1_title=None,
            filename_fallback="x.md",
            defaults_kind="doc",
        )
    assert "tags" in str(excinfo.value)
    assert "annotations-config" in str(excinfo.value)


def test_frontmatter_related_not_a_list_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        merge(
            annotations_entry=None,
            frontmatter={"related": "single-string"},
            h1_title=None,
            filename_fallback="x.md",
            defaults_kind="doc",
        )
    assert "related" in str(excinfo.value)
    assert "frontmatter" in str(excinfo.value)


def test_annotations_related_not_a_list_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        merge(
            annotations_entry={"related": "x"},
            frontmatter={},
            h1_title=None,
            filename_fallback="x.md",
            defaults_kind="doc",
        )
    assert "related" in str(excinfo.value)
    assert "annotations-config" in str(excinfo.value)


def test_tags_with_non_string_element_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        merge(
            annotations_entry=None,
            frontmatter={"tags": [1, 2]},
            h1_title=None,
            filename_fallback="x.md",
            defaults_kind="doc",
        )
    assert "tags" in str(excinfo.value)


def test_annotations_related_with_non_string_element_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        merge(
            annotations_entry={"related": ["ok", 42]},
            frontmatter={},
            h1_title=None,
            filename_fallback="x.md",
            defaults_kind="doc",
        )
    assert "related" in str(excinfo.value)
    assert "annotations-config" in str(excinfo.value)


def test_yaml_parsed_date_in_frontmatter_is_coerced_to_string() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={"last_confirmed_at": dt.date(2026, 5, 1)},
        h1_title=None,
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result.last_confirmed_at == "2026-05-01"
    assert result.provenance["last_confirmed_at"] == "frontmatter"


def test_empty_h1_title_falls_through_to_filename() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={},
        h1_title="",
        filename_fallback="from-file.md",
        defaults_kind="doc",
    )
    assert result.title == "from-file.md"
    assert result.provenance["title"] == "default"


def test_both_sides_supply_empty_lists_provenance_is_default() -> None:
    result = merge(
        annotations_entry={"tags": [], "related": []},
        frontmatter={"tags": [], "related": []},
        h1_title="H",
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert result.tags == ()
    assert result.related == ()
    assert result.provenance["tags"] == "default"
    assert result.provenance["related"] == "default"


def test_merged_annotation_is_frozen() -> None:
    result = merge(
        annotations_entry=None,
        frontmatter={},
        h1_title="H",
        filename_fallback="x.md",
        defaults_kind="doc",
    )
    assert isinstance(result, MergedAnnotation)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.kind = "service"  # type: ignore[misc]
