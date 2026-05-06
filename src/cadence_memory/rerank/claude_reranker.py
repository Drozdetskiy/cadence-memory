"""ClaudeReranker: invokes Claude via ClaudeRunner to rerank query candidates."""

from __future__ import annotations

import importlib.resources
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from string import Template

from cadence_memory.executor.claude_executor import ClaudeRunner
from cadence_memory.rerank.interface import RerankedItem, RerankItem

__all__ = ["ClaudeReranker"]


_FENCED_JSON_RE = re.compile(
    r"^```(?:json)?\s*(?P<body>.*?)\s*```$",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class ClaudeReranker:
    runner: ClaudeRunner
    model: str
    _template: Template = field(init=False, repr=False)

    def __post_init__(self) -> None:
        template_text = (
            importlib.resources.files("cadence_memory.defaults.prompts")
            .joinpath("rerank.txt")
            .read_text(encoding="utf-8")
        )
        self._template = Template(template_text)

    def rerank(
        self,
        query: str,
        items: Sequence[RerankItem],
    ) -> list[RerankedItem]:
        if not items:
            return []

        items_payload = [
            {
                "chunk_id": item.chunk_id,
                "title": item.title,
                "heading_path": list(item.heading_path),
                "summary": item.summary if item.summary else item.body_excerpt,
            }
            for item in items
        ]
        prompt = self._template.substitute(
            query=query,
            count=len(items),
            items_json=json.dumps(items_payload, ensure_ascii=False, indent=2),
        )
        env = {**os.environ, "ANTHROPIC_MODEL": self.model}

        try:
            result = self.runner.run(prompt, env=env)
        except Exception as exc:
            return self._passthrough(items, f"runner raised {type(exc).__name__}: {exc}")

        if result.idle_timed_out:
            return self._passthrough(items, "claude exceeded the idle timeout")
        if result.exit_code != 0:
            return self._passthrough(items, f"claude exited with status {result.exit_code}")

        payload = _strip_fences(result.output.strip())
        if not payload:
            return self._passthrough(items, "claude returned empty output")

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            return self._passthrough(items, f"invalid JSON output: {exc}")

        if not isinstance(parsed, list):
            return self._passthrough(items, "invalid JSON output: top-level value must be a list")

        score_map: dict[str, float] = {}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            chunk_id = entry.get("chunk_id")
            score = entry.get("score")
            if not isinstance(chunk_id, str):
                continue
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            score_map[chunk_id] = max(0.0, min(10.0, float(score)))

        input_order = {item.chunk_id: idx for idx, item in enumerate(items)}
        missing = [item.chunk_id for item in items if item.chunk_id not in score_map]
        if missing:
            print(
                f"rerank: {len(missing)} chunk_id(s) missing from response, assigning score 0.0",
                file=sys.stderr,
            )
            for chunk_id in missing:
                score_map[chunk_id] = 0.0

        ranked_ids = sorted(
            (item.chunk_id for item in items),
            key=lambda cid: (-score_map[cid], input_order[cid]),
        )
        return [
            RerankedItem(chunk_id=cid, score=score_map[cid], rank=rank)
            for rank, cid in enumerate(ranked_ids)
        ]

    def _passthrough(
        self,
        items: Sequence[RerankItem],
        message: str,
    ) -> list[RerankedItem]:
        print(f"rerank: {message}", file=sys.stderr)
        return [
            RerankedItem(
                chunk_id=item.chunk_id,
                score=5.0 - rank * 0.1,
                rank=rank,
            )
            for rank, item in enumerate(items)
        ]


def _strip_fences(text: str) -> str:
    match = _FENCED_JSON_RE.match(text)
    if match is not None:
        return match.group("body").strip()
    return text
