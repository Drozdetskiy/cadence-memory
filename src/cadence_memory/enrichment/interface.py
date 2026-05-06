"""Enricher Protocol and EnrichmentResult dataclass."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

__all__ = ["Enricher", "EnrichmentResult"]


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    keywords: tuple[str, ...]
    questions: tuple[str, ...]
    alt_phrasings: tuple[str, ...]
    model: str
    generated_at: str

    def to_index_text(self) -> str:
        return " | ".join((*self.keywords, *self.questions, *self.alt_phrasings))

    def to_json(self) -> str:
        payload = {
            "keywords": list(self.keywords),
            "questions": list(self.questions),
            "alt_phrasings": list(self.alt_phrasings),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(
        cls,
        text: str,
        *,
        model: str,
        generated_at: str,
    ) -> EnrichmentResult:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("enrichment payload must be a JSON object")
        keywords = _coerce_str_tuple(parsed.get("keywords"))
        questions = _coerce_str_tuple(parsed.get("questions"))
        alt_phrasings = _coerce_str_tuple(parsed.get("alt_phrasings"))
        return cls(
            keywords=keywords,
            questions=questions,
            alt_phrasings=alt_phrasings,
            model=model,
            generated_at=generated_at,
        )


def _coerce_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("expected a JSON list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("expected a JSON list of strings")
        stripped = item.strip()
        if stripped:
            out.append(stripped)
    return tuple(out)


class Enricher(Protocol):
    model: str

    def enrich_chunk(
        self,
        *,
        title: str,
        heading_path: tuple[str, ...],
        body: str,
        summary: str | None,
    ) -> EnrichmentResult: ...
