"""Event dataclasses and JSONL parser for claude stream-json (design2 §6.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass

_INPUT_SUMMARY_MAX = 120
_CONTENT_SUMMARY_MAX = 200


@dataclass(frozen=True, slots=True)
class SystemEvent:
    subtype: str
    session_id: str | None


@dataclass(frozen=True, slots=True)
class AssistantTextEvent:
    text: str


@dataclass(frozen=True, slots=True)
class ToolUseEvent:
    tool: str
    input_summary: str


@dataclass(frozen=True, slots=True)
class ToolResultEvent:
    is_error: bool
    content_summary: str


@dataclass(frozen=True, slots=True)
class ResultEvent:
    subtype: str | None
    total_cost_usd: float | None
    num_turns: int | None
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    message: str


Event = SystemEvent | AssistantTextEvent | ToolUseEvent | ToolResultEvent | ResultEvent | ErrorEvent


def parse_event(line: str) -> Event | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    event_type = obj.get("type")
    if event_type == "system":
        subtype_raw = obj.get("subtype", "")
        subtype = subtype_raw if isinstance(subtype_raw, str) else ""
        session_id_raw = obj.get("session_id")
        session_id = session_id_raw if isinstance(session_id_raw, str) else None
        return SystemEvent(subtype=subtype, session_id=session_id)

    if event_type == "assistant":
        return _parse_assistant(obj)

    if event_type == "user":
        return _parse_user(obj)

    if event_type == "result":
        subtype_raw = obj.get("subtype")
        subtype_val = subtype_raw if isinstance(subtype_raw, str) else None
        cost_raw = obj.get("total_cost_usd")
        cost: float | None = float(cost_raw) if isinstance(cost_raw, (int, float)) else None
        turns_raw = obj.get("num_turns")
        turns: int | None = int(turns_raw) if isinstance(turns_raw, int) else None
        duration_raw = obj.get("duration_ms")
        duration: int | None = int(duration_raw) if isinstance(duration_raw, int) else None
        return ResultEvent(
            subtype=subtype_val,
            total_cost_usd=cost,
            num_turns=turns,
            duration_ms=duration,
        )

    if event_type == "error":
        message_raw = obj.get("message", "")
        message = message_raw if isinstance(message_raw, str) else ""
        return ErrorEvent(message=message)

    return None


def _parse_assistant(obj: dict[str, object]) -> Event | None:
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None

    text_parts: list[str] = []
    first_tool_use: tuple[str, dict[str, object]] | None = None
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text_val = block.get("text", "")
            if isinstance(text_val, str):
                text_parts.append(text_val)
        elif block_type == "tool_use" and first_tool_use is None:
            tool_name_raw = block.get("name", "")
            tool_name = tool_name_raw if isinstance(tool_name_raw, str) else ""
            tool_input_raw = block.get("input", {})
            tool_input = tool_input_raw if isinstance(tool_input_raw, dict) else {}
            first_tool_use = (tool_name, tool_input)

    if text_parts:
        return AssistantTextEvent(text="".join(text_parts))
    if first_tool_use is not None:
        tool_name, tool_input = first_tool_use
        return ToolUseEvent(
            tool=tool_name,
            input_summary=_summarize_input(tool_name, tool_input),
        )
    return None


def _parse_user(obj: dict[str, object]) -> Event | None:
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            is_error_raw = block.get("is_error", False)
            is_error = bool(is_error_raw) if isinstance(is_error_raw, bool) else False
            return ToolResultEvent(
                is_error=is_error,
                content_summary=_summarize_content(block.get("content")),
            )
    return None


def _summarize_input(tool: str, inp: dict[str, object]) -> str:
    if tool == "Read":
        path = inp.get("path") or inp.get("file_path") or ""
        return str(path)
    if tool in ("Edit", "Write"):
        file_path = inp.get("file_path", "")
        body_raw = inp.get("new_string") if tool == "Edit" else inp.get("content")
        body = body_raw if isinstance(body_raw, str) else ""
        snippet = body[:60]
        return f"{file_path}: {snippet}"
    if tool == "Bash":
        command_raw = inp.get("command", "")
        command = command_raw if isinstance(command_raw, str) else ""
        return command[:80]
    return json.dumps(inp)[:_INPUT_SUMMARY_MAX]


def _summarize_content(content: object) -> str:
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_val = block.get("text", "")
                if isinstance(text_val, str):
                    text_parts.append(text_val)
        joined = "".join(text_parts)
        return joined[:_CONTENT_SUMMARY_MAX]
    if isinstance(content, str):
        return content[:_CONTENT_SUMMARY_MAX]
    return json.dumps(content)[:_CONTENT_SUMMARY_MAX]
