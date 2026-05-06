"""Unit tests for cadence_memory.query.identifiers."""

from __future__ import annotations

import dataclasses

import pytest

from cadence_memory.query import QueryIdentifiers, extract_identifiers


def test_extracts_jira_id() -> None:
    ids = extract_identifiers("see ticket ZEAL-10367 for details")
    assert ids.jira == ("ZEAL-10367",)
    assert ids.release == ()
    assert ids.acceptance == ()
    assert ids.schemas == ()
    assert ids.endpoints == ()
    assert not ids.is_empty()


def test_extracts_camel_case_schemas() -> None:
    ids = extract_identifiers("найди BillingEntity и SubscriptionResponse")
    assert ids.schemas == ("BillingEntity", "SubscriptionResponse")
    assert ids.jira == ()


def test_extracts_release_id() -> None:
    ids = extract_identifiers("какой R1-3.1 был сделан")
    assert ids.release == ("R1-3.1",)
    assert ids.jira == ()
    assert ids.acceptance == ()


def test_extracts_acceptance_id() -> None:
    ids = extract_identifiers("AC-BAC-1.1 acceptance")
    assert ids.acceptance == ("AC-BAC-1.1",)
    assert ids.jira == ()
    assert ids.release == ()


def test_extracts_endpoint() -> None:
    ids = extract_identifiers("POST /v1/customers description")
    assert ids.endpoints == ("POST /v1/customers",)
    assert ids.schemas == ()


def test_plain_text_is_empty() -> None:
    ids = extract_identifiers("просто человеческий запрос без кода")
    assert ids.is_empty()
    assert ids.all_targets() == set()


def test_no_false_positives_hello_world() -> None:
    ids = extract_identifiers("Hello World")
    assert ids.is_empty()


def test_no_false_positives_single_word() -> None:
    ids = extract_identifiers("BAR1")
    assert ids.is_empty()


def test_no_false_positives_lowercase_dash() -> None:
    ids = extract_identifiers("abc-def")
    assert ids.is_empty()


def test_multi_kind_query() -> None:
    text = "BillingEntity for ZEAL-10367 in R1-3.1 via POST /v1/billing AC-BAC-1.1"
    ids = extract_identifiers(text)
    assert ids.schemas == ("BillingEntity",)
    assert ids.jira == ("ZEAL-10367",)
    assert ids.release == ("R1-3.1",)
    assert ids.acceptance == ("AC-BAC-1.1",)
    assert ids.endpoints == ("POST /v1/billing",)
    assert ids.all_targets() == {
        "BillingEntity",
        "ZEAL-10367",
        "R1-3.1",
        "POST /v1/billing",
        "AC-BAC-1.1",
    }


def test_dedupes_repeated_identifiers() -> None:
    text = "BillingEntity ZEAL-10367 BillingEntity ZEAL-10367 POST /v1/x POST /v1/x R1-3.1 R1-3.1"
    ids = extract_identifiers(text)
    assert ids.schemas == ("BillingEntity",)
    assert ids.jira == ("ZEAL-10367",)
    assert ids.endpoints == ("POST /v1/x",)
    assert ids.release == ("R1-3.1",)


def test_acceptance_does_not_leak_into_jira() -> None:
    ids = extract_identifiers("AC-FOO-12.34 spec")
    assert ids.acceptance == ("AC-FOO-12.34",)
    assert ids.jira == ()


def test_query_identifiers_is_frozen_dataclass() -> None:
    ids = QueryIdentifiers(
        jira=("ZEAL-1",),
        release=(),
        acceptance=(),
        schemas=(),
        endpoints=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ids.jira = ("OTHER-1",)  # type: ignore[misc]


def test_endpoint_strips_trailing_punctuation() -> None:
    ids = extract_identifiers("call POST /v1/customers, then GET /v1/users.")
    assert ids.endpoints == ("POST /v1/customers", "GET /v1/users")


def test_endpoint_supports_head_and_options() -> None:
    ids = extract_identifiers("HEAD /v1/foo and OPTIONS /v1/bar")
    assert ids.endpoints == ("HEAD /v1/foo", "OPTIONS /v1/bar")
