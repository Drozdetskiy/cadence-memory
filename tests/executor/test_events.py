"""Tests for the JSONL line parser and event dataclasses."""

from __future__ import annotations

import json

from cadence_memory.executor.events import (
    AssistantTextEvent,
    ErrorEvent,
    ResultEvent,
    SystemEvent,
    ToolResultEvent,
    ToolUseEvent,
    parse_event,
)


def test_parse_system_event() -> None:
    line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc"})
    event = parse_event(line)
    assert event == SystemEvent(subtype="init", session_id="abc")


def test_parse_assistant_text_single_block() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}],
            },
        }
    )
    event = parse_event(line)
    assert event == AssistantTextEvent(text="hello")


def test_parse_assistant_text_multiple_blocks() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hello "},
                    {"type": "text", "text": "world"},
                ],
            },
        }
    )
    event = parse_event(line)
    assert event == AssistantTextEvent(text="hello world")


def test_parse_tool_use_read() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"path": "/tmp/foo.py"},
                    }
                ],
            },
        }
    )
    event = parse_event(line)
    assert event == ToolUseEvent(tool="Read", input_summary="/tmp/foo.py")


def test_parse_tool_use_edit() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {
                            "file_path": "/tmp/x.py",
                            "old_string": "old",
                            "new_string": "this is the new content snippet",
                        },
                    }
                ],
            },
        }
    )
    event = parse_event(line)
    assert isinstance(event, ToolUseEvent)
    assert event.tool == "Edit"
    assert "/tmp/x.py" in event.input_summary
    assert "this is the new content snippet" in event.input_summary


def test_parse_tool_use_write_uses_content() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {
                            "file_path": "/tmp/x.py",
                            "content": "fresh file body",
                        },
                    }
                ],
            },
        }
    )
    event = parse_event(line)
    assert isinstance(event, ToolUseEvent)
    assert event.tool == "Write"
    assert "fresh file body" in event.input_summary


def test_parse_tool_use_bash_truncated() -> None:
    long_command = "echo " + "x" * 200
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": long_command},
                    }
                ],
            },
        }
    )
    event = parse_event(line)
    assert isinstance(event, ToolUseEvent)
    assert event.tool == "Bash"
    assert len(event.input_summary) == 80


def test_parse_tool_use_unknown_tool_falls_back_to_json() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Banana",
                        "input": {"k": "v"},
                    }
                ],
            },
        }
    )
    event = parse_event(line)
    assert isinstance(event, ToolUseEvent)
    assert event.tool == "Banana"
    assert "k" in event.input_summary and "v" in event.input_summary


def test_parse_tool_result_success() -> None:
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "is_error": False,
                        "content": [{"type": "text", "text": "ok result"}],
                    }
                ],
            },
        }
    )
    event = parse_event(line)
    assert event == ToolResultEvent(is_error=False, content_summary="ok result")


def test_parse_tool_result_error_truncates_content() -> None:
    big_text = "y" * 500
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "is_error": True,
                        "content": big_text,
                    }
                ],
            },
        }
    )
    event = parse_event(line)
    assert isinstance(event, ToolResultEvent)
    assert event.is_error is True
    assert len(event.content_summary) == 200


def test_parse_result_event() -> None:
    line = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 0.012,
            "num_turns": 3,
            "duration_ms": 1234,
        }
    )
    event = parse_event(line)
    assert event == ResultEvent(
        subtype="success",
        total_cost_usd=0.012,
        num_turns=3,
        duration_ms=1234,
    )


def test_parse_result_event_missing_keys_become_none() -> None:
    line = json.dumps({"type": "result"})
    event = parse_event(line)
    assert event == ResultEvent(
        subtype=None,
        total_cost_usd=None,
        num_turns=None,
        duration_ms=None,
    )


def test_parse_error_event() -> None:
    line = json.dumps({"type": "error", "message": "boom"})
    event = parse_event(line)
    assert event == ErrorEvent(message="boom")


def test_parse_unknown_type_returns_none() -> None:
    line = json.dumps({"type": "banana"})
    assert parse_event(line) is None


def test_parse_malformed_json_returns_none() -> None:
    assert parse_event("not json{") is None


def test_parse_empty_line_returns_none() -> None:
    assert parse_event("") is None
    assert parse_event("   \n  ") is None


def test_parse_user_echo_returns_none() -> None:
    line = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": "hi"},
        }
    )
    assert parse_event(line) is None


def test_parse_assistant_text_wins_over_tool_use_on_same_message() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "thinking"},
                    {"type": "tool_use", "name": "Read", "input": {"path": "/x"}},
                ],
            },
        }
    )
    event = parse_event(line)
    assert event == AssistantTextEvent(text="thinking")
