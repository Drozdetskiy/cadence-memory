"""Split markdown bodies into chunks by H1/H2 headings, code-fence aware."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

DEFAULT_MAX_CHUNK_TOKENS = 2000
MAX_SUMMARY_CHARS = 400
_MIN_SUMMARY_CHARS = 20
_PREAMBLE_SLUG = "_preamble"
_SCHEMAS_SLUG = "_schemas"
_PREAMBLE_MIN_BYTES = 500

_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_CODE_FENCE_RE = re.compile(r"^\s*```")
_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
ENDPOINT_HEADER_RE = re.compile(r"^##\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/\S+)")
_SCHEMA_SECTION_TITLES = frozenset({"schemas", "models", "components"})

_OPEN_WHEN_RE = re.compile(r"\bOpen when\b[^\n]+", re.IGNORECASE)
_LABEL_TRIGGER_RE = re.compile(
    r"\b(?:Purpose|Overview|Summary)\s*:\s*([^\n]+)",
    re.IGNORECASE,
)
_HEADING_LINE_RE = re.compile(r"^\s*#+\s+")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_BOLD_TITLE_LINE_RE = re.compile(r"^\s*\*\*[^*]+\*\*\s*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class Chunk:
    slug: str
    heading_path: tuple[str, ...]
    body: str
    order: int
    summary: str | None = None


def _finalize_summary(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS] + "…"
    if len(text) < _MIN_SUMMARY_CHARS:
        return None
    return text


def _strip_code_fences(body: str) -> str:
    out: list[str] = []
    in_code = False
    for line in body.splitlines():
        if _CODE_FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        out.append(line)
    return "\n".join(out)


def _strip_markdown_noise(body: str) -> str:
    out: list[str] = []
    in_code = False
    for line in body.splitlines():
        if _CODE_FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        if (
            _HEADING_LINE_RE.match(line)
            or _LIST_MARKER_RE.match(line)
            or _BOLD_TITLE_LINE_RE.match(line)
        ):
            continue
        out.append(line)
    return "\n".join(out)


def _first_prose_paragraph(body: str) -> str | None:
    paragraphs: list[list[str]] = [[]]
    in_code = False
    for line in body.splitlines():
        if _CODE_FENCE_RE.match(line):
            in_code = not in_code
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        if in_code:
            continue
        stripped = line.strip()
        if not stripped:
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        if (
            _HEADING_LINE_RE.match(line)
            or _LIST_MARKER_RE.match(line)
            or _BOLD_TITLE_LINE_RE.match(line)
        ):
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        paragraphs[-1].append(stripped)
    for para in paragraphs:
        if para:
            return " ".join(para)
    return None


def _first_two_sentences(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    parts = _SENTENCE_SPLIT_RE.split(stripped, maxsplit=2)
    return " ".join(parts[:2]).strip() or None


def extract_summary(body: str) -> str | None:
    if not body or not body.strip():
        return None

    no_code = _strip_code_fences(body)

    open_when_match = _OPEN_WHEN_RE.search(no_code)
    if open_when_match is not None:
        candidate = _finalize_summary(open_when_match.group(0))
        if candidate is not None:
            return candidate

    label_match = _LABEL_TRIGGER_RE.search(no_code)
    if label_match is not None:
        candidate = _finalize_summary(label_match.group(1))
        if candidate is not None:
            return candidate

    paragraph = _first_prose_paragraph(body)
    if paragraph is not None:
        candidate = _finalize_summary(paragraph)
        if candidate is not None:
            return candidate

    two_sentences = _first_two_sentences(_strip_markdown_noise(body))
    if two_sentences is not None:
        candidate = _finalize_summary(two_sentences)
        if candidate is not None:
            return candidate

    return None


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
    kind: str | None = None,
    doc_id: str | None = None,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
) -> list[Chunk]:
    if kind == "api-spec":
        return _chunk_api_spec(body, doc_id=doc_id, max_tokens=max_tokens)
    return _chunk_generic(body, max_tokens=max_tokens)


def _chunk_generic(
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
                summary=extract_summary(piece_body),
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
                    summary=extract_summary(piece_body),
                )
            )
            order += 1

    return chunks


def _chunk_api_spec(
    body: str,
    *,
    doc_id: str | None,
    max_tokens: int,
) -> list[Chunk]:
    lines = body.splitlines(keepends=True)
    in_code = False

    preamble_lines: list[str] = []
    endpoint_sections: list[tuple[str, str, list[str]]] = []
    schemas_lines: list[str] = []

    state = "preamble"
    h1_title: str | None = None

    for line in lines:
        in_code_after = _toggle_code_fence(line, in_code)
        is_fence = in_code_after != in_code

        if state == "schemas":
            # Schemas section runs from the schemas header to EOF; any later
            # endpoint or schemas headers fold into this chunk so the document
            # bytes stay in source order.
            schemas_lines.append(line)
            in_code = in_code_after
            continue

        endpoint_match: re.Match[str] | None = None
        is_schemas_header = False
        if not in_code and not is_fence:
            endpoint_match = ENDPOINT_HEADER_RE.match(line)
            if endpoint_match is None:
                h2_match = _H2_RE.match(line)
                if h2_match is not None:
                    title = h2_match.group(1).strip()
                    if title.lower() in _SCHEMA_SECTION_TITLES:
                        is_schemas_header = True

        if endpoint_match is not None:
            method = endpoint_match.group(1)
            path = endpoint_match.group(2)
            endpoint_sections.append((method, path, [line]))
            state = "endpoint"
        elif is_schemas_header:
            schemas_lines.append(line)
            state = "schemas"
        else:
            if state == "preamble":
                if h1_title is None and not in_code and not is_fence:
                    h1_match = _H1_RE.match(line)
                    if h1_match is not None:
                        h1_title = h1_match.group(1).strip()
                preamble_lines.append(line)
            else:  # state == "endpoint"
                endpoint_sections[-1][2].append(line)

        in_code = in_code_after

    if not endpoint_sections:
        prefix = f"doc {doc_id} " if doc_id is not None else ""
        print(
            f"warn: {prefix}kind=api-spec but no endpoint headers found, using generic chunker",
            file=sys.stderr,
        )
        return _chunk_generic(body, max_tokens=max_tokens)

    used_slugs: set[str] = set()
    chunks: list[Chunk] = []
    order = 0

    preamble_body = "".join(preamble_lines)
    preamble_heading_path: tuple[str, ...] = (h1_title,) if h1_title is not None else ()
    for piece_body in _split_section_body(preamble_body, max_tokens):
        slug = _dedupe_slug(_PREAMBLE_SLUG, used_slugs)
        chunks.append(
            Chunk(
                slug=slug,
                heading_path=preamble_heading_path,
                body=piece_body,
                order=order,
                summary=extract_summary(piece_body),
            )
        )
        order += 1

    for method, path, ep_lines in endpoint_sections:
        section_body = "".join(ep_lines)
        base_slug = slugify(f"{method} {path}")
        endpoint_heading_path = ("ENDPOINTS", f"{method} {path}")
        for piece_body in _split_section_body(section_body, max_tokens):
            slug = _dedupe_slug(base_slug, used_slugs)
            chunks.append(
                Chunk(
                    slug=slug,
                    heading_path=endpoint_heading_path,
                    body=piece_body,
                    order=order,
                    summary=extract_summary(piece_body),
                )
            )
            order += 1

    if schemas_lines:
        schemas_body = "".join(schemas_lines)
        schemas_heading_path = ("SCHEMAS",)
        for piece_body in _split_section_body(schemas_body, max_tokens):
            slug = _dedupe_slug(_SCHEMAS_SLUG, used_slugs)
            chunks.append(
                Chunk(
                    slug=slug,
                    heading_path=schemas_heading_path,
                    body=piece_body,
                    order=order,
                    summary=extract_summary(piece_body),
                )
            )
            order += 1

    return chunks
