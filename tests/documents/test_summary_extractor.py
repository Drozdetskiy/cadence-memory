"""Unit tests for the chunk summary extractor."""

from __future__ import annotations

from cadence_memory.documents.chunker import (
    MAX_SUMMARY_CHARS,
    extract_summary,
)


def test_open_when_trigger_returns_open_when_sentence() -> None:
    body = (
        "**Agent Title**\n\n"
        "Open when you need to fetch user data from the upstream cache layer.\n\n"
        "Some other paragraph that should not be picked.\n"
    )
    summary = extract_summary(body)
    assert summary is not None
    assert summary.startswith("Open when")
    assert "fetch user data" in summary
    assert "Some other paragraph" not in summary


def test_purpose_label_strips_prefix_and_returns_content() -> None:
    body = (
        "# Doc\n\n"
        "Purpose: explain how the indexer wires chunks to the FTS table for fast lookups.\n"
    )
    summary = extract_summary(body)
    assert summary == ("explain how the indexer wires chunks to the FTS table for fast lookups.")


def test_overview_label_strips_prefix() -> None:
    body = "Overview: this document covers the chunk extraction pipeline end-to-end.\n"
    summary = extract_summary(body)
    assert summary == "this document covers the chunk extraction pipeline end-to-end."


def test_summary_label_strips_prefix() -> None:
    body = "Summary: a concise description of the architecture and its tradeoffs.\n"
    summary = extract_summary(body)
    assert summary == "a concise description of the architecture and its tradeoffs."


def test_first_paragraph_after_h1_used_when_no_triggers() -> None:
    body = (
        "# Title\n\n"
        "This is the first ordinary prose paragraph that explains the document.\n\n"
        "Second paragraph should not appear here.\n"
    )
    summary = extract_summary(body)
    assert summary == ("This is the first ordinary prose paragraph that explains the document.")


def test_leading_code_fence_after_h1_skipped_to_next_paragraph() -> None:
    body = (
        "# Title\n\n"
        "```python\n"
        "def example():\n"
        "    pass\n"
        "```\n\n"
        "This real paragraph follows the code fence and should be picked.\n"
    )
    summary = extract_summary(body)
    assert summary == ("This real paragraph follows the code fence and should be picked.")


def test_list_markers_skipped_to_first_prose_paragraph() -> None:
    body = (
        "# Title\n\n"
        "- item one\n"
        "- item two\n\n"
        "This is the first real prose paragraph after the list block above.\n"
    )
    summary = extract_summary(body)
    assert summary == ("This is the first real prose paragraph after the list block above.")


def test_short_body_returns_none() -> None:
    assert extract_summary("# T\n\n.") is None


def test_empty_body_returns_none() -> None:
    assert extract_summary("") is None
    assert extract_summary("   \n\n   ") is None


def test_result_shorter_than_min_returns_none() -> None:
    body = "# Title\n\nshort.\n"
    assert extract_summary(body) is None


def test_long_paragraph_truncated_to_max_with_ellipsis() -> None:
    long_text = "lorem ipsum dolor sit amet " * 50
    body = f"# Doc\n\n{long_text}\n"
    summary = extract_summary(body)
    assert summary is not None
    assert summary.endswith("…")
    assert len(summary) == MAX_SUMMARY_CHARS + 1


def test_whitespace_trimmed() -> None:
    body = "# Doc\n\n    This paragraph has leading and trailing whitespace galore.    \n"
    summary = extract_summary(body)
    assert summary == ("This paragraph has leading and trailing whitespace galore.")


def test_open_when_takes_precedence_over_paragraph() -> None:
    body = (
        "**Title**\n\n"
        "Open when you want a precise example of trigger precedence in action.\n\n"
        "This other paragraph would otherwise win the fallback selection.\n"
    )
    summary = extract_summary(body)
    assert summary is not None
    assert summary.startswith("Open when")


def test_bold_only_title_line_skipped() -> None:
    body = (
        "**Document Title**\n\nThe first real prose paragraph follows the bold-only title above.\n"
    )
    summary = extract_summary(body)
    assert summary == ("The first real prose paragraph follows the bold-only title above.")


def test_trigger_phrases_inside_code_fence_are_ignored() -> None:
    body = (
        "# Title\n\n"
        "```\n"
        "# Purpose: this is inside code and must not become the summary.\n"
        "Open when this should also not match because it's in a code fence.\n"
        "```\n\n"
        "The real prose paragraph that should win the extractor.\n"
    )
    summary = extract_summary(body)
    assert summary == "The real prose paragraph that should win the extractor."


def test_no_prose_returns_none_instead_of_raw_markdown() -> None:
    body = (
        "# Heading One\n\n"
        "## Heading Two\n\n"
        "- list item one\n"
        "- list item two\n\n"
        "```\n"
        "code line\n"
        "```\n"
    )
    assert extract_summary(body) is None
