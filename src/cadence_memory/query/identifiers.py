"""Regex-based extraction of identifier tokens (JIRA / release / acceptance / schema / endpoint)."""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "AC_RE",
    "ENDPOINT_RE",
    "JIRA_RE",
    "RELEASE_RE",
    "SCHEMA_RE",
    "QueryIdentifiers",
    "extract_identifiers",
]


JIRA_RE = re.compile(r"\b[A-Z]{2,}-\d{2,}\b")
RELEASE_RE = re.compile(r"\bR\d+-\d+(?:\.\d+)?\b")
AC_RE = re.compile(r"\bAC-[A-Z]+-\d+\.\d+\b")
SCHEMA_RE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+){1,}\b")
ENDPOINT_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[\w/{}.-]+)")


@dataclass(frozen=True, slots=True)
class QueryIdentifiers:
    jira: tuple[str, ...]
    release: tuple[str, ...]
    acceptance: tuple[str, ...]
    schemas: tuple[str, ...]
    endpoints: tuple[str, ...]

    def is_empty(self) -> bool:
        return not (self.jira or self.release or self.acceptance or self.schemas or self.endpoints)

    def all_targets(self) -> set[str]:
        return {
            *self.jira,
            *self.release,
            *self.acceptance,
            *self.schemas,
            *self.endpoints,
        }


def _dedupe_ordered(matches: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in matches:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def extract_identifiers(text: str) -> QueryIdentifiers:
    """Run all identifier regexes against ``text`` and return a deduped result.

    Acceptance IDs are matched before JIRA, and any JIRA match whose span
    falls inside an acceptance match is dropped — otherwise an input like
    ``AC-FOO-12.34`` would surface a spurious ``FOO-12`` JIRA hit.
    """
    acceptance_matches = list(AC_RE.finditer(text))
    acceptance_spans = [m.span() for m in acceptance_matches]
    acceptance = _dedupe_ordered([m.group(0) for m in acceptance_matches])

    release = _dedupe_ordered(RELEASE_RE.findall(text))

    jira_hits: list[str] = []
    for match in JIRA_RE.finditer(text):
        start, end = match.span()
        if any(a_start <= start and end <= a_end for a_start, a_end in acceptance_spans):
            continue
        jira_hits.append(match.group(0))
    jira = _dedupe_ordered(jira_hits)

    schemas = _dedupe_ordered(SCHEMA_RE.findall(text))

    endpoints = _dedupe_ordered(
        [f"{method} {path.rstrip('.,;:!?)')}" for method, path in ENDPOINT_RE.findall(text)]
    )

    return QueryIdentifiers(
        jira=jira,
        release=release,
        acceptance=acceptance,
        schemas=schemas,
        endpoints=endpoints,
    )
