"""ClaudeEnricher: invokes Claude via ClaudeRunner to generate chunk enrichments."""

from __future__ import annotations

import importlib.resources
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from string import Template

from cadence_memory.enrichment.interface import EnrichmentResult
from cadence_memory.executor.claude_executor import ClaudeRunner

__all__ = ["MAX_KEYWORDS", "ClaudeEnricher"]

MAX_KEYWORDS = 25
_MAX_QUESTIONS = 25
_MAX_ALT_PHRASINGS = 25

_FENCED_JSON_RE = re.compile(
    r"^```(?:json)?\s*(?P<body>.*?)\s*```$",
    re.DOTALL | re.IGNORECASE,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ClaudeEnricher:
    runner: ClaudeRunner
    model: str
    now: Callable[[], datetime] = _utcnow
    _template: Template = field(init=False, repr=False)

    def __post_init__(self) -> None:
        template_text = (
            importlib.resources.files("cadence_memory.defaults.prompts")
            .joinpath("enrichment.txt")
            .read_text(encoding="utf-8")
        )
        self._template = Template(template_text)

    def enrich_chunk(
        self,
        *,
        title: str,
        heading_path: tuple[str, ...],
        body: str,
        summary: str | None,
    ) -> EnrichmentResult:
        generated_at = self.now().isoformat()
        prompt = self._template.substitute(
            title=title,
            heading_path=" / ".join(heading_path) if heading_path else "(none)",
            summary=summary if summary else "(none)",
            body=body,
        )
        env = {**os.environ, "ANTHROPIC_MODEL": self.model}

        try:
            result = self.runner.run(prompt, env=env)
        except Exception as exc:
            return self._fail(generated_at, f"runner raised {type(exc).__name__}: {exc}")

        if result.idle_timed_out:
            return self._fail(generated_at, "claude exceeded the idle timeout")
        if result.exit_code != 0:
            return self._fail(generated_at, f"claude exited with status {result.exit_code}")

        payload = _strip_fences(result.output.strip())
        if not payload:
            return self._fail(generated_at, "claude returned empty output")

        try:
            parsed = EnrichmentResult.from_json(
                payload,
                model=self.model,
                generated_at=generated_at,
            )
        except (ValueError, TypeError) as exc:
            return self._fail(generated_at, f"invalid JSON output: {exc}")

        return EnrichmentResult(
            keywords=parsed.keywords[:MAX_KEYWORDS],
            questions=parsed.questions[:_MAX_QUESTIONS],
            alt_phrasings=parsed.alt_phrasings[:_MAX_ALT_PHRASINGS],
            model=self.model,
            generated_at=generated_at,
        )

    def _fail(self, generated_at: str, message: str) -> EnrichmentResult:
        print(f"enrichment: {message}", file=sys.stderr)
        return EnrichmentResult(
            keywords=(),
            questions=(),
            alt_phrasings=(),
            model=self.model,
            generated_at=generated_at,
        )


def _strip_fences(text: str) -> str:
    match = _FENCED_JSON_RE.match(text)
    if match is not None:
        return match.group("body").strip()
    return text
