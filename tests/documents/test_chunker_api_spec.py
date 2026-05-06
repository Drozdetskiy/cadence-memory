"""Unit tests for the api-spec branch of the markdown chunker."""

from __future__ import annotations

import pytest

from cadence_memory.documents.chunker import chunk_markdown

FIXTURE = (
    "# Billing Engine API\n"
    "\n"
    "## Authentication\n"
    "Use X-API-Key header.\n"
    "\n"
    "## GET /v1/customers\n"
    "List all customers.\n"
    "**Response:** `Customer[]`\n"
    "\n"
    "## POST /v1/customers\n"
    "Create a customer.\n"
    "**Request body:** `CustomerCreate`\n"
    "\n"
    "## DELETE /v1/customers/{id}\n"
    "Delete a customer.\n"
    "\n"
    "## Schemas\n"
    "### Customer\n"
    "Customer schema body.\n"
)


def test_api_spec_happy_path_five_chunks() -> None:
    chunks = chunk_markdown(FIXTURE, kind="api-spec")
    slugs = [c.slug for c in chunks]
    assert slugs == [
        "_preamble",
        "get-v1-customers",
        "post-v1-customers",
        "delete-v1-customers-id",
        "_schemas",
    ]


def test_api_spec_heading_paths() -> None:
    chunks = chunk_markdown(FIXTURE, kind="api-spec")
    by_slug = {c.slug: c for c in chunks}
    assert by_slug["_preamble"].heading_path == ("Billing Engine API",)
    assert by_slug["get-v1-customers"].heading_path == (
        "ENDPOINTS",
        "GET /v1/customers",
    )
    assert by_slug["post-v1-customers"].heading_path == (
        "ENDPOINTS",
        "POST /v1/customers",
    )
    assert by_slug["delete-v1-customers-id"].heading_path == (
        "ENDPOINTS",
        "DELETE /v1/customers/{id}",
    )
    assert by_slug["_schemas"].heading_path == ("SCHEMAS",)


def test_api_spec_orders_are_monotonic_and_cover_body() -> None:
    chunks = chunk_markdown(FIXTURE, kind="api-spec")
    orders = [c.order for c in chunks]
    assert orders == list(range(len(chunks)))

    joined = "".join(c.body for c in chunks)
    assert joined == FIXTURE


def test_api_spec_curly_brace_path_slug_normalised() -> None:
    chunks = chunk_markdown(FIXTURE, kind="api-spec")
    slugs = [c.slug for c in chunks]
    assert "delete-v1-customers-id" in slugs


@pytest.mark.parametrize("schemas_title", ["Schemas", "Models", "Components"])
def test_api_spec_schemas_variants_recognised(schemas_title: str) -> None:
    body = f"# API\n\n## GET /v1/foo\nGet foo.\n\n## {schemas_title}\nSchema body.\n"
    chunks = chunk_markdown(body, kind="api-spec")
    slugs = [c.slug for c in chunks]
    assert slugs == ["_preamble", "get-v1-foo", "_schemas"]
    schemas_chunk = chunks[-1]
    assert schemas_chunk.heading_path == ("SCHEMAS",)
    assert f"## {schemas_title}" in schemas_chunk.body


def test_api_spec_schemas_case_insensitive() -> None:
    body = "# API\n\n## GET /v1/foo\nGet foo.\n\n## models\nlowercase models heading.\n"
    chunks = chunk_markdown(body, kind="api-spec")
    assert chunks[-1].slug == "_schemas"


def test_api_spec_no_endpoints_falls_back_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = "# Some Doc\n\nJust prose.\n\n## Overview\n\nNothing here.\n"
    chunks = chunk_markdown(body, kind="api-spec", doc_id="proj:doc.md")

    captured = capsys.readouterr()
    assert "warn:" in captured.err
    assert "doc proj:doc.md" in captured.err
    assert "kind=api-spec" in captured.err
    assert "no endpoint headers" in captured.err

    generic = chunk_markdown(body)
    assert [c.slug for c in chunks] == [c.slug for c in generic]
    assert [c.heading_path for c in chunks] == [c.heading_path for c in generic]


def test_api_spec_no_endpoints_warning_omits_doc_id_when_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = "# Doc\n\nNo endpoints.\n"
    chunk_markdown(body, kind="api-spec")
    captured = capsys.readouterr()
    assert "warn:" in captured.err
    assert "doc " not in captured.err


def test_generic_kind_on_api_fixture_uses_generic_chunker() -> None:
    api_chunks = chunk_markdown(FIXTURE, kind="api-spec")
    doc_chunks = chunk_markdown(FIXTURE, kind="doc")

    api_slugs = [c.slug for c in api_chunks]
    doc_slugs = [c.slug for c in doc_chunks]
    assert api_slugs != doc_slugs

    assert len(api_chunks) == 5
    assert len(doc_chunks) > len(api_chunks)
    assert "billing-engine-api" in doc_slugs
    assert "authentication" in doc_slugs

    api_paths = {c.heading_path for c in api_chunks}
    doc_paths = {c.heading_path for c in doc_chunks}
    assert ("ENDPOINTS", "GET /v1/customers") in api_paths
    assert ("ENDPOINTS", "GET /v1/customers") not in doc_paths
    assert ("SCHEMAS",) in api_paths
    assert ("SCHEMAS",) not in doc_paths


def test_chunk_markdown_no_kwargs_still_works() -> None:
    body = "# Doc\n\n## Section\n\nbody.\n"
    chunks = chunk_markdown(body)
    assert [c.slug for c in chunks] == ["_preamble", "doc", "section"]


def test_api_spec_endpoint_header_inside_code_fence_not_split() -> None:
    body = (
        "# API\n"
        "\n"
        "## GET /v1/foo\n"
        "real endpoint.\n"
        "\n"
        "```\n"
        "## POST /v1/fake\n"
        "this is example code, not a real endpoint\n"
        "```\n"
        "\n"
        "still part of GET /v1/foo.\n"
    )
    chunks = chunk_markdown(body, kind="api-spec")
    slugs = [c.slug for c in chunks]
    assert slugs == ["_preamble", "get-v1-foo"]
    foo = chunks[1]
    assert "## POST /v1/fake" in foo.body
    assert "still part of GET /v1/foo." in foo.body


def test_api_spec_oversized_endpoint_subsplit_by_h3() -> None:
    big = "lorem ipsum dolor sit amet " * 200
    body = (
        "# API\n"
        "\n"
        "## GET /v1/customers\n"
        "Endpoint summary.\n"
        "\n"
        "### Parameters\n"
        f"{big}\n"
        "\n"
        "### Response\n"
        f"{big}\n"
    )
    chunks = chunk_markdown(body, kind="api-spec", max_tokens=100)
    customer_chunks = [c for c in chunks if c.slug.startswith("get-v1-customers")]
    assert len(customer_chunks) >= 2
    assert customer_chunks[0].slug == "get-v1-customers"
    assert customer_chunks[1].slug == "get-v1-customers-2"
    for c in customer_chunks:
        assert c.heading_path == ("ENDPOINTS", "GET /v1/customers")


def test_api_spec_no_h1_in_preamble_yields_empty_heading_path() -> None:
    body = "Some intro without H1.\n\n## GET /v1/foo\nendpoint body.\n"
    chunks = chunk_markdown(body, kind="api-spec")
    assert chunks[0].slug == "_preamble"
    assert chunks[0].heading_path == ()
    assert chunks[1].slug == "get-v1-foo"


def test_api_spec_preamble_includes_pre_endpoint_h2_section() -> None:
    chunks = chunk_markdown(FIXTURE, kind="api-spec")
    preamble = chunks[0]
    assert "## Authentication" in preamble.body
    assert "X-API-Key" in preamble.body
    assert "## GET /v1/customers" not in preamble.body


def test_api_spec_endpoint_header_after_schemas_folds_into_schemas() -> None:
    body = (
        "# API\n"
        "\n"
        "## GET /v1/foo\n"
        "foo body.\n"
        "\n"
        "## Schemas\n"
        "schema body.\n"
        "\n"
        "## POST /v1/bar\n"
        "stray endpoint after schemas.\n"
    )
    chunks = chunk_markdown(body, kind="api-spec")
    slugs = [c.slug for c in chunks]
    assert slugs == ["_preamble", "get-v1-foo", "_schemas"]
    schemas_chunk = chunks[-1]
    assert "## POST /v1/bar" in schemas_chunk.body
    assert "stray endpoint after schemas." in schemas_chunk.body
    assert "".join(c.body for c in chunks) == body


def test_api_spec_schemas_before_endpoints_falls_back(
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = (
        "# API\n"
        "\n"
        "## Schemas\n"
        "schema body.\n"
        "\n"
        "## GET /v1/foo\n"
        "foo body after schemas.\n"
    )
    chunks = chunk_markdown(body, kind="api-spec")
    captured = capsys.readouterr()
    assert "warn:" in captured.err
    slugs = [c.slug for c in chunks]
    assert "_schemas" not in slugs
