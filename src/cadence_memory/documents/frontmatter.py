"""Wiki page frontmatter parser (design2 §4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast, get_args

import frontmatter
import yaml

_BOM = "\ufeff"

PageType = Literal[
    "model",
    "service",
    "controller",
    "architecture",
    "decision",
    "pattern",
    "overview",
    "log",
    "gaps",
]

Confidence = Literal["high", "medium", "low"]

_PAGE_TYPES: tuple[str, ...] = get_args(PageType)
_CONFIDENCES: tuple[str, ...] = get_args(Confidence)

_REQUIRED_FIELDS: tuple[str, ...] = (
    "title",
    "type",
    "project",
    "created",
    "updated",
    "tags",
    "confidence",
)


@dataclass(frozen=True, slots=True)
class PageFrontmatter:
    """Validated frontmatter block of a wiki page (design2 §4)."""

    title: str
    type: PageType
    source: str | None
    project: str
    created: date
    updated: date
    tags: tuple[str, ...]
    confidence: Confidence


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """A wiki markdown page after frontmatter parsing."""

    path: Path
    frontmatter: PageFrontmatter
    body: str


class FrontmatterError(Exception):
    """Raised when a wiki page's frontmatter is missing, malformed, or invalid."""

    __slots__ = ("message", "path")

    path: Path
    message: str

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _normalize_text(text: str) -> str:
    if text.startswith(_BOM):
        text = text[len(_BOM) :]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_normalized(path: Path) -> str:
    raw = path.read_bytes().decode("utf-8", errors="replace")
    return _normalize_text(raw)


def parse_page(path: Path) -> ParsedPage:
    """Parse a wiki markdown page from disk. Raises `FrontmatterError` on any problem."""
    try:
        text = _read_normalized(path)
    except FileNotFoundError as exc:
        raise FrontmatterError(path, "file not found") from exc
    return parse_text(text, path=path)


def parse_text(text: str, *, path: Path) -> ParsedPage:
    """Parse a wiki markdown page from an in-memory string."""
    normalized = _normalize_text(text)
    try:
        post = frontmatter.loads(normalized)
    except (yaml.YAMLError, TypeError) as exc:
        raise FrontmatterError(path, f"invalid YAML in frontmatter: {exc}") from exc

    metadata = cast(dict[str, object], post.metadata)
    if not metadata:
        raise FrontmatterError(path, "missing frontmatter block")

    for key in _REQUIRED_FIELDS:
        if key not in metadata:
            raise FrontmatterError(path, f"missing required field: {key}")

    title = _require_nonempty_str(metadata["title"], "title", path)
    page_type = _require_literal(metadata["type"], "type", _PAGE_TYPES, path)
    source = _parse_optional_nonempty_str(metadata.get("source"), "source", path)
    project = _require_nonempty_str(metadata["project"], "project", path)
    created = _parse_date(metadata["created"], "created", path)
    updated = _parse_date(metadata["updated"], "updated", path)
    tags = _parse_tag_list(metadata["tags"], "tags", path)
    confidence = _require_literal(metadata["confidence"], "confidence", _CONFIDENCES, path)

    fm = PageFrontmatter(
        title=title,
        type=cast(PageType, page_type),
        source=source,
        project=project,
        created=created,
        updated=updated,
        tags=tags,
        confidence=cast(Confidence, confidence),
    )
    body = post.content.lstrip("\n")
    return ParsedPage(path=path, frontmatter=fm, body=body)


def _require_nonempty_str(value: object, key: str, path: Path) -> str:
    if not isinstance(value, str) or not value:
        raise FrontmatterError(path, f"{key}: must be a non-empty string")
    return value


def _parse_optional_nonempty_str(value: object, key: str, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise FrontmatterError(path, f"{key}: must be a non-empty string or absent")
    return value


def _require_literal(value: object, key: str, allowed: tuple[str, ...], path: Path) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise FrontmatterError(path, f"{key}: must be one of {list(allowed)}, got {value!r}")
    return value


def _parse_date(value: object, key: str, path: Path) -> date:
    if isinstance(value, datetime):
        raise FrontmatterError(path, f"{key}: must be a date (got datetime)")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise FrontmatterError(path, f"{key}: invalid ISO-8601 date {value!r}") from exc
    raise FrontmatterError(path, f"{key}: must be a date or ISO-8601 string")


def _parse_tag_list(value: object, key: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise FrontmatterError(path, f"{key}: must be a list of strings")
    items = cast(list[object], value)
    out: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, str) or not item:
            raise FrontmatterError(path, f"{key}[{i}]: must be a non-empty string")
        out.append(item)
    return tuple(out)
