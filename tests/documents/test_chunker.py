"""Unit tests for the markdown chunker."""

from __future__ import annotations

from cadence_memory.documents.chunker import (
    DEFAULT_MAX_CHUNK_TOKENS,
    Chunk,
    chunk_markdown,
    slugify,
)


def test_slugify_lowercases_and_collapses_punctuation() -> None:
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_strips_leading_and_trailing_dashes() -> None:
    assert slugify("---foo---") == "foo"


def test_slugify_unicode_ascii_only() -> None:
    assert slugify("Café résumé") == "caf-rsum"


def test_slugify_empty_falls_back_to_section() -> None:
    assert slugify("") == "section"
    assert slugify("!!!") == "section"


def test_slugify_collapses_multiple_separators() -> None:
    assert slugify("foo   bar___baz") == "foo-bar-baz"


def test_chunk_markdown_no_headings_returns_single_preamble() -> None:
    body = "Just plain prose.\nNo headings here.\n"
    chunks = chunk_markdown(body)
    assert len(chunks) == 1
    assert chunks[0].slug == "_preamble"
    assert chunks[0].heading_path == ()
    assert chunks[0].body == body
    assert chunks[0].order == 0


def test_chunk_markdown_empty_body_returns_single_preamble() -> None:
    chunks = chunk_markdown("")
    assert len(chunks) == 1
    assert chunks[0].slug == "_preamble"
    assert chunks[0].body == ""
    assert chunks[0].order == 0


def test_chunk_markdown_h1_h2_h3_structure() -> None:
    body = (
        "Intro paragraph.\n\n"
        "# Title\n\n"
        "Lead-in.\n\n"
        "## First Section\n\n"
        "Section body.\n\n"
        "### Subsection\n\n"
        "Sub body.\n\n"
        "## Second Section\n\n"
        "Second body.\n"
    )
    chunks = chunk_markdown(body)
    assert chunks[0].slug == "_preamble"
    assert chunks[0].heading_path == ()
    slugs = [c.slug for c in chunks[1:]]
    assert slugs == ["title", "first-section", "second-section"]
    assert chunks[1].heading_path == ("Title",)
    assert chunks[2].heading_path == ("Title", "First Section")
    assert chunks[3].heading_path == ("Title", "Second Section")
    assert "### Subsection" in chunks[2].body
    assert [c.order for c in chunks] == list(range(len(chunks)))


def test_chunk_markdown_bold_title_then_h1_keeps_preamble_with_bold() -> None:
    body = "**Document Title**\n\n# Real Heading\n\nReal body.\n"
    chunks = chunk_markdown(body)
    assert chunks[0].slug == "_preamble"
    assert "**Document Title**" in chunks[0].body
    assert chunks[1].slug == "real-heading"
    assert chunks[1].heading_path == ("Real Heading",)


def test_chunk_markdown_preamble_extended_to_500_bytes_when_heading_near_start() -> None:
    body = "# Very First Heading\n\n" + ("filler line\n" * 60)
    chunks = chunk_markdown(body)
    assert chunks[0].slug == "_preamble"
    assert len(chunks[0].body.encode("utf-8")) >= 500


def test_chunk_markdown_code_fence_with_faux_heading_not_split() -> None:
    body = (
        "# Real\n\n"
        "Some text.\n\n"
        "```\n"
        "## Faux Heading\n"
        "code line\n"
        "```\n\n"
        "More text.\n"
    )
    chunks = chunk_markdown(body)
    assert [c.slug for c in chunks] == ["_preamble", "real"]
    real = chunks[1]
    assert "## Faux Heading" in real.body
    assert "```" in real.body


def test_chunk_markdown_duplicate_h2_gets_suffix() -> None:
    body = (
        "# Doc\n\n"
        "## Overview\n\n"
        "First overview.\n\n"
        "## Overview\n\n"
        "Second overview.\n"
    )
    chunks = chunk_markdown(body)
    slugs = [c.slug for c in chunks[1:]]
    assert slugs == ["doc", "overview", "overview-2"]


def test_chunk_markdown_long_h2_subsplit_by_h3() -> None:
    para = "lorem ipsum " * 200
    body = (
        "# Doc\n\n"
        "## Big Section\n\n"
        f"{para}\n\n"
        "### Sub A\n\n"
        f"{para}\n\n"
        "### Sub B\n\n"
        f"{para}\n"
    )
    chunks = chunk_markdown(body, max_tokens=100)
    big_chunks = [c for c in chunks if c.slug.startswith("big-section")]
    assert len(big_chunks) >= 2
    assert big_chunks[0].slug == "big-section"
    assert big_chunks[1].slug == "big-section-2"
    for c in big_chunks:
        assert c.heading_path == ("Doc", "Big Section")


def test_chunk_markdown_long_headingless_body_subsplit_into_preamble_pieces() -> None:
    paragraph = "word " * 300
    body = f"{paragraph}\n\n{paragraph}\n\n{paragraph}\n"
    chunks = chunk_markdown(body, max_tokens=200)
    assert len(chunks) >= 2
    assert chunks[0].slug == "_preamble"
    assert all(c.heading_path == () for c in chunks)
    assert [c.slug for c in chunks[1:]] == [f"_preamble-{i}" for i in range(2, len(chunks) + 1)]
    assert [c.order for c in chunks] == list(range(len(chunks)))


def test_chunk_markdown_long_section_no_h3_splits_by_paragraphs() -> None:
    paragraph = "word " * 300
    body = (
        "# Doc\n\n"
        "## Long\n\n"
        f"{paragraph}\n\n"
        f"{paragraph}\n\n"
        f"{paragraph}\n"
    )
    chunks = chunk_markdown(body, max_tokens=200)
    long_chunks = [c for c in chunks if c.slug.startswith("long")]
    assert len(long_chunks) >= 2


def test_chunk_markdown_does_not_split_inside_code_fence() -> None:
    long_code = "code line\n" * 500
    body = "# Doc\n\n## Section\n\n```\n" + long_code + "```\n"
    chunks = chunk_markdown(body, max_tokens=100)
    section_chunks = [c for c in chunks if c.slug.startswith("section")]
    assert section_chunks
    fence_chunk = next(c for c in section_chunks if "code line" in c.body)
    fence_count = fence_chunk.body.count("```")
    assert fence_count == 2, "fence must be opened and closed in the same chunk"
    assert fence_chunk.body.count("code line") == 500


def test_chunk_markdown_orders_are_monotonic() -> None:
    body = "# A\n\nx\n\n## B\n\ny\n\n## C\n\nz\n"
    chunks = chunk_markdown(body)
    orders = [c.order for c in chunks]
    assert orders == sorted(orders)
    assert orders == list(range(len(chunks)))


def test_chunk_markdown_default_max_tokens_constant() -> None:
    assert DEFAULT_MAX_CHUNK_TOKENS == 2000


def test_chunk_dataclass_is_frozen() -> None:
    c = Chunk(slug="x", heading_path=("a",), body="b", order=0)
    try:
        c.slug = "y"  # type: ignore[misc]
    except (AttributeError, TypeError):
        return
    raise AssertionError("Chunk must be frozen")


def test_chunk_markdown_heading_with_punctuation_slugified() -> None:
    body = "# Hello, World!\n\nbody\n"
    chunks = chunk_markdown(body)
    assert chunks[1].slug == "hello-world"
    assert chunks[1].heading_path == ("Hello, World!",)


def test_chunk_markdown_h2_before_h1_uses_h2_as_path_root() -> None:
    body = "## Standalone\n\nbody\n"
    chunks = chunk_markdown(body)
    assert chunks[1].slug == "standalone"
    assert chunks[1].heading_path == ("Standalone",)
