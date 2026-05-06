"""Query expansion via Claude: generate alternative phrasings for FTS5 search."""

from __future__ import annotations

import importlib.resources
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from string import Template
from typing import Protocol, cast

from cadence_memory.executor.claude_executor import ClaudeRunner
from cadence_memory.store.interface import Store

__all__ = ["ClaudeQueryExpander", "ExpansionResult", "QueryExpander"]


_FENCED_JSON_RE = re.compile(
    r"^```(?:json)?\s*(?P<body>.*?)\s*```$",
    re.DOTALL | re.IGNORECASE,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ExpansionResult:
    original: str
    variants: tuple[str, ...]
    model: str


class QueryExpander(Protocol):
    def expand(self, query: str, *, max_variants: int = 3) -> ExpansionResult: ...


@dataclass
class ClaudeQueryExpander:
    runner: ClaudeRunner
    model: str
    store: Store
    now: Callable[[], datetime] = _utcnow
    _template: Template = field(init=False, repr=False)

    def __post_init__(self) -> None:
        template_text = (
            importlib.resources.files("cadence_memory.defaults.prompts")
            .joinpath("expansion.txt")
            .read_text(encoding="utf-8")
        )
        self._template = Template(template_text)

    def expand(self, query: str, *, max_variants: int = 3) -> ExpansionResult:
        cached = self.store.expansion_cache_get(query, self.model)
        if cached is not None:
            cached_variants = _parse_variants_json(cached.get("variants_json"))
            if cached_variants is not None:
                clamped = cached_variants[:max_variants]
                return ExpansionResult(
                    original=query,
                    variants=(query, *clamped),
                    model=self.model,
                )

        prompt = self._template.substitute(query=query, max_variants=max_variants)
        env = {**os.environ, "ANTHROPIC_MODEL": self.model}

        try:
            result = self.runner.run(prompt, env=env)
        except Exception as exc:
            return self._fail(query, f"runner raised {type(exc).__name__}: {exc}")

        if result.idle_timed_out:
            return self._fail(query, "claude exceeded the idle timeout")
        if result.exit_code != 0:
            return self._fail(query, f"claude exited with status {result.exit_code}")

        payload = _strip_fences(result.output.strip())
        if not payload:
            return self._fail(query, "claude returned empty output")

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            return self._fail(query, f"invalid JSON output: {exc}")

        if not isinstance(parsed, dict) or "variants" not in parsed:
            return self._fail(query, "invalid JSON output: missing 'variants' key")

        raw = parsed["variants"]
        if not isinstance(raw, list):
            return self._fail(query, "invalid JSON output: 'variants' must be a list")

        variants = _normalize_variants(raw, original=query, max_variants=max_variants)
        if not variants:
            return self._fail(query, "no usable variants returned")

        generated_at = self.now().isoformat()
        self.store.expansion_cache_put(
            query_text=query,
            model=self.model,
            variants_json=json.dumps(list(variants), ensure_ascii=False),
            generated_at=generated_at,
        )

        return ExpansionResult(
            original=query,
            variants=(query, *variants),
            model=self.model,
        )

    def _fail(self, query: str, message: str) -> ExpansionResult:
        print(f"expansion: {message}", file=sys.stderr)
        return ExpansionResult(
            original=query,
            variants=(query,),
            model=self.model,
        )


def _parse_variants_json(raw: object) -> tuple[str, ...] | None:
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, list):
        return None
    out: list[str] = []
    for item in decoded:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                out.append(stripped)
    return tuple(out)


def _normalize_variants(
    raw: list[object],
    *,
    original: str,
    max_variants: int,
) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if not stripped or stripped == original or stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
        if len(out) >= max_variants:
            break
    return tuple(out)


def _strip_fences(text: str) -> str:
    match = _FENCED_JSON_RE.match(text)
    if match is not None:
        return cast(str, match.group("body")).strip()
    return text
