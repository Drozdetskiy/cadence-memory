"""Split markdown bodies into chunks by H1/H2 headings, code-fence aware."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_MAX_CHUNK_TOKENS = 2000
_PREAMBLE_SLUG = "_preamble"
_PREAMBLE_MIN_BYTES = 500

_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_CODE_FENCE_RE = re.compile(r"^\s*```")
_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Chunk:
    slug: str
    heading_path: tuple[str, ...]
    body: str
    order: int


def slugify(text: str) -> str:
    ascii_text = text.encode("ascii", "ignore").decode("ascii").lower()
    collapsed = _SLUG_NON_ALNUM_RE.sub("-", ascii_text).strip("-")
    return collapsed or "section"


def _approx_tokens(body: str) -> int:
    return len(body) // 4


def _classify_heading(line: str, in_code: bool) -> tuple[int, str] | None:
    if in_code:
        return None
    if (m := _H1_RE.match(line)) is not None:
        return 1, m.group(1).strip()
    if (m := _H2_RE.match(line)) is not None:
        return 2, m.group(1).strip()
    return None


def _toggle_code_fence(line: str, in_code: bool) -> bool:
    if _CODE_FENCE_RE.match(line):
        return not in_code
    return in_code


def _dedupe_slug(slug: str, used: set[str]) -> str:
    if slug not in used:
        used.add(slug)
        return slug
    n = 2
    while True:
        candidate = f"{slug}-{n}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


def _split_section_body(body: str, max_tokens: int) -> list[str]:
    if _approx_tokens(body) <= max_tokens:
        return [body]

    h3_pieces = _split_by_h3(body)
    if len(h3_pieces) > 1:
        result: list[str] = []
        for piece in h3_pieces:
            result.extend(_split_section_body(piece, max_tokens))
        return result

    paragraph_pieces = _split_by_paragraphs(body, max_tokens)
    if len(paragraph_pieces) > 1:
        return paragraph_pieces

    return [body]


def _split_by_h3(body: str) -> list[str]:
    lines = body.splitlines(keepends=True)
    in_code = False
    sections: list[list[str]] = [[]]
    for line in lines:
        in_code_after = _toggle_code_fence(line, in_code)
        is_fence = in_code_after != in_code
        if not in_code and not is_fence and _H3_RE.match(line):
            sections.append([line])
        else:
            sections[-1].append(line)
        in_code = in_code_after
    return ["".join(s) for s in sections if "".join(s)]


def _split_by_paragraphs(body: str, max_tokens: int) -> list[str]:
    lines = body.splitlines(keepends=True)
    in_code = False
    blocks: list[list[str]] = [[]]
    for line in lines:
        in_code_after = _toggle_code_fence(line, in_code)
        if not in_code and not in_code_after and line.strip() == "":
            if blocks[-1]:
                blocks.append([])
            blocks[-1].append(line)
        else:
            blocks[-1].append(line)
        in_code = in_code_after

    grouped: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for block in blocks:
        text = "".join(block)
        if not text:
            continue
        block_tokens = _approx_tokens(text)
        if current and current_tokens + block_tokens > max_tokens:
            grouped.append("".join(current))
            current = [text]
            current_tokens = block_tokens
        else:
            current.append(text)
            current_tokens += block_tokens
    if current:
        grouped.append("".join(current))
    return grouped or [body]


def chunk_markdown(
    body: str,
    *,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
) -> list[Chunk]:
    lines = body.splitlines(keepends=True)
    in_code = False

    preamble_lines: list[str] = []
    sections: list[tuple[int, str, list[str]]] = []
    current_section: tuple[int, str, list[str]] | None = None

    first_heading_byte_offset: int | None = None
    byte_offset = 0
    for line in lines:
        in_code_after = _toggle_code_fence(line, in_code)
        heading: tuple[int, str] | None = None
        if not in_code and in_code_after == in_code:
            heading = _classify_heading(line, in_code)
        if (
            heading is not None
            and current_section is None
            and not sections
            and first_heading_byte_offset is None
        ):
            first_heading_byte_offset = byte_offset

        if heading is not None:
            depth, title = heading
            if current_section is not None:
                sections.append(current_section)
            current_section = (depth, title, [line])
        else:
            if current_section is None:
                preamble_lines.append(line)
            else:
                current_section[2].append(line)
        in_code = in_code_after
        byte_offset += len(line.encode("utf-8"))

    if current_section is not None:
        sections.append(current_section)

    preamble_body = "".join(preamble_lines)
    if (
        sections
        and len(preamble_body.encode("utf-8")) < _PREAMBLE_MIN_BYTES
        and first_heading_byte_offset is not None
    ):
        # Extend preamble to include up to the first 500 bytes of the document
        # (helps ``**TITLE**`` style files where a real H1 follows immediately).
        encoded = body.encode("utf-8")
        target = min(_PREAMBLE_MIN_BYTES, len(encoded))
        preamble_body = encoded[:target].decode("utf-8", errors="ignore")

    used_slugs: set[str] = set()
    chunks: list[Chunk] = []
    order = 0

    preamble_pieces = _split_section_body(preamble_body, max_tokens)
    for piece_body in preamble_pieces:
        slug = _dedupe_slug(_PREAMBLE_SLUG, used_slugs)
        chunks.append(
            Chunk(
                slug=slug,
                heading_path=(),
                body=piece_body,
                order=order,
            )
        )
        order += 1

    h1_path: tuple[str, ...] = ()
    for depth, title, section_lines in sections:
        section_body = "".join(section_lines)
        if depth == 1:
            heading_path: tuple[str, ...] = (title,)
            h1_path = (title,)
        else:
            heading_path = (*h1_path, title) if h1_path else (title,)

        base_slug = slugify(title)
        pieces = _split_section_body(section_body, max_tokens)
        for piece_body in pieces:
            slug = _dedupe_slug(base_slug, used_slugs)
            chunks.append(
                Chunk(
                    slug=slug,
                    heading_path=heading_path,
                    body=piece_body,
                    order=order,
                )
            )
            order += 1

    return chunks
