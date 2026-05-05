"""Wrap python-frontmatter to produce a ParsedDocument with frontmatter, body, and h1 title."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter
import yaml

# Allows a missing trailing newline at EOF and trailing whitespace on either
# fence — python-frontmatter accepts those, so the slicer must too, otherwise
# frontmatter_hash silently collapses to sha256("") for inputs the parser
# accepts but the slicer misses.
_FRONTMATTER_BLOCK = re.compile(
    r"\A(---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z))",
    re.DOTALL,
)
_H1 = re.compile(r"^#\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    frontmatter: dict[str, object]
    frontmatter_text: str
    body: str
    h1_title: str | None


def _find_h1(body: str) -> str | None:
    for line in body.splitlines():
        match = _H1.match(line)
        if match:
            return match.group(1).strip()
    return None


def parse_text(text: str) -> ParsedDocument:
    try:
        post = frontmatter.loads(text)
    except yaml.YAMLError as err:
        raise ValueError(f"invalid YAML frontmatter: {err}") from err
    metadata = dict(post.metadata)
    match = _FRONTMATTER_BLOCK.match(text)
    if match:
        frontmatter_text = match.group(1)
        body = text[match.end() :]
    elif metadata:
        raise ValueError(
            "frontmatter parsed but the opening '---' fence is not at the "
            "start of the input"
        )
    else:
        frontmatter_text = ""
        body = text
    return ParsedDocument(
        frontmatter=metadata,
        frontmatter_text=frontmatter_text,
        body=body,
        h1_title=_find_h1(body),
    )


def parse_file(path: Path) -> ParsedDocument:
    text = path.read_text(encoding="utf-8")
    try:
        return parse_text(text)
    except ValueError as err:
        raise ValueError(f"{path}: {err}") from err
