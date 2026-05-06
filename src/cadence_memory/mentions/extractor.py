"""Regex-based extraction of code-path, endpoint, schema, and doc-link mentions."""

from __future__ import annotations

import re

from cadence_memory.store.interface import Mention

__all__ = ["extract_mentions"]


CODE_PATH_RE = re.compile(
    r"(?<![:/\w.])(?<!\]\()(\.\/)?((?:[a-z_][\w-]*/){1,8}[a-z_][\w-]*"
    r"\.(?:swift|yaml|json|java|tsx|jsx|hpp|cpp|sql|yml|php"
    r"|py|ts|js|go|rs|md|kt|rb|cs|sh|c|h)\b)"
    r"(?::(\d+)(?:-(\d+))?)?",
    re.IGNORECASE,
)

ENDPOINT_RE = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[\w/{}.-]+)",
)

SCHEMA_NAME_RE = re.compile(
    r"`([A-Z][a-z]+(?:[A-Z][a-z0-9]+){1,})`"
    r"|"
    r"\b(?:schema|model)\s+`?([A-Z][a-z]+(?:[A-Z][a-z0-9]+){1,})`?"
)

DOC_LINK_RE = re.compile(r"\[(?:[^\]]+)\]\(([^)]+\.md)(?:#[^)]*)?\)")


def extract_mentions(body: str) -> list[Mention]:
    """Return a deduped, deterministically ordered list of mentions in body.

    Iteration order across kinds: code → endpoint → schema → doc.
    Within a kind, matches are emitted in their document-order of first
    occurrence. Dedup key is ``(target, target_kind, line_range)``.
    """
    seen: set[tuple[str, str, str | None]] = set()
    out: list[Mention] = []

    for match in CODE_PATH_RE.finditer(body):
        target = match.group(2)
        line_start = match.group(3)
        line_end = match.group(4)
        if line_end is not None:
            line_range: str | None = f"{line_start}-{line_end}"
        elif line_start is not None:
            line_range = line_start
        else:
            line_range = None
        key = (target, "code", line_range)
        if key in seen:
            continue
        seen.add(key)
        out.append(Mention(target=target, target_kind="code", line_range=line_range))

    for match in ENDPOINT_RE.finditer(body):
        method = match.group(1)
        path = match.group(2).rstrip(".,;:!?)")
        target = f"{method} {path}"
        key = (target, "endpoint", None)
        if key in seen:
            continue
        seen.add(key)
        out.append(Mention(target=target, target_kind="endpoint"))

    for match in SCHEMA_NAME_RE.finditer(body):
        target = match.group(1) or match.group(2)
        if target is None:
            continue
        key = (target, "schema", None)
        if key in seen:
            continue
        seen.add(key)
        out.append(Mention(target=target, target_kind="schema"))

    for match in DOC_LINK_RE.finditer(body):
        target = match.group(1)
        if target.startswith(("http://", "https://")):
            continue
        key = (target, "doc", None)
        if key in seen:
            continue
        seen.add(key)
        out.append(Mention(target=target, target_kind="doc"))

    return out
