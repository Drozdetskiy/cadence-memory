"""Tests for the regex-based mentions extractor."""

from __future__ import annotations

import pytest

from cadence_memory.mentions import extract_mentions
from cadence_memory.store.interface import Mention


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "see src/foo.py for details",
            [Mention(target="src/foo.py", target_kind="code")],
        ),
        (
            "see ./src/foo.py for details",
            [Mention(target="src/foo.py", target_kind="code")],
        ),
        (
            "see src/foo.py:123 for details",
            [Mention(target="src/foo.py", target_kind="code", line_range="123")],
        ),
        (
            "see src/foo.py:100-200 for details",
            [Mention(target="src/foo.py", target_kind="code", line_range="100-200")],
        ),
        (
            "deep path apps/portal/src/lib/dal/dal.ts here",
            [Mention(target="apps/portal/src/lib/dal/dal.ts", target_kind="code")],
        ),
    ],
)
def test_code_path_matches(body: str, expected: list[Mention]) -> None:
    assert extract_mentions(body) == expected


@pytest.mark.parametrize(
    "ext",
    ["py", "ts", "tsx", "go", "rs", "json", "yaml", "sql", "md"],
)
def test_code_path_extensions(ext: str) -> None:
    body = f"see pkg/sub/file.{ext} for details"
    mentions = extract_mentions(body)
    assert mentions == [Mention(target=f"pkg/sub/file.{ext}", target_kind="code")]


@pytest.mark.parametrize(
    "body",
    [
        "see foo.py for details",
        "see https://example.com/y.py for details",
        "see pkg/bar.exe for details",
    ],
)
def test_code_path_negative(body: str) -> None:
    mentions = [m for m in extract_mentions(body) if m.target_kind == "code"]
    assert mentions == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "see packages/my-app/index.ts for details",
            [Mention(target="packages/my-app/index.ts", target_kind="code")],
        ),
        (
            "look at apps/my-pkg/sub-dir/file.go here",
            [Mention(target="apps/my-pkg/sub-dir/file.go", target_kind="code")],
        ),
    ],
)
def test_code_path_hyphenated_directories(body: str, expected: list[Mention]) -> None:
    assert extract_mentions(body) == expected


def test_markdown_link_to_subdirectory_md_does_not_double_classify() -> None:
    body = "see [link](docs/architecture.md) for the architecture"
    mentions = extract_mentions(body)
    assert mentions == [Mention(target="docs/architecture.md", target_kind="doc")]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "calls GET /api/v1/users to fetch them",
            [Mention(target="GET /api/v1/users", target_kind="endpoint")],
        ),
        (
            "calls POST /v1/customers/{id} with payload",
            [Mention(target="POST /v1/customers/{id}", target_kind="endpoint")],
        ),
        (
            "DELETE /things/{id}/sub removes the sub-resource",
            [Mention(target="DELETE /things/{id}/sub", target_kind="endpoint")],
        ),
    ],
)
def test_endpoint_matches(body: str, expected: list[Mention]) -> None:
    assert extract_mentions(body) == expected


def test_endpoint_method_case_sensitive() -> None:
    body = "calls get /api/v1/users to fetch them"
    assert [m for m in extract_mentions(body) if m.target_kind == "endpoint"] == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "describes `BillingEntity` in detail",
            [Mention(target="BillingEntity", target_kind="schema")],
        ),
        (
            "see schema BillingEntity below",
            [Mention(target="BillingEntity", target_kind="schema")],
        ),
        (
            "see model SubscriptionResponse below",
            [Mention(target="SubscriptionResponse", target_kind="schema")],
        ),
    ],
)
def test_schema_matches(body: str, expected: list[Mention]) -> None:
    assert extract_mentions(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "describes BillingEntity in detail",
        "lower-case bigword should not match",
        "single Foo word",
    ],
)
def test_schema_negative(body: str) -> None:
    mentions = [m for m in extract_mentions(body) if m.target_kind == "schema"]
    assert mentions == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "see [link](other.md) for more",
            [Mention(target="other.md", target_kind="doc")],
        ),
        (
            "see [link](other.md#section) for more",
            [Mention(target="other.md", target_kind="doc")],
        ),
    ],
)
def test_doc_link_matches(body: str, expected: list[Mention]) -> None:
    mentions = [m for m in extract_mentions(body) if m.target_kind == "doc"]
    assert mentions == expected


@pytest.mark.parametrize(
    "body",
    [
        "see [external](https://example.com) for more",
        "see [external](https://example.com/foo.md) for more",
        "see [other](other.txt) for more",
    ],
)
def test_doc_link_negative(body: str) -> None:
    mentions = [m for m in extract_mentions(body) if m.target_kind == "doc"]
    assert mentions == []


def test_dedup_same_code_path_twice() -> None:
    body = "first src/foo.py and again src/foo.py"
    assert extract_mentions(body) == [Mention(target="src/foo.py", target_kind="code")]


def test_distinct_line_ranges_kept() -> None:
    body = "see src/foo.py:10 and src/foo.py:20-30"
    mentions = extract_mentions(body)
    assert mentions == [
        Mention(target="src/foo.py", target_kind="code", line_range="10"),
        Mention(target="src/foo.py", target_kind="code", line_range="20-30"),
    ]


def test_combined_body_returns_all_kinds_in_order() -> None:
    body = (
        "Implemented in src/services/stripe.py:42 and exposed via "
        "POST /v1/customers. The payload uses `BillingEntity`. "
        "See [details](billing.md#refunds)."
    )
    mentions = extract_mentions(body)
    assert mentions == [
        Mention(
            target="src/services/stripe.py",
            target_kind="code",
            line_range="42",
        ),
        Mention(target="POST /v1/customers", target_kind="endpoint"),
        Mention(target="BillingEntity", target_kind="schema"),
        Mention(target="billing.md", target_kind="doc"),
    ]


def test_empty_body_returns_no_mentions() -> None:
    assert extract_mentions("") == []
