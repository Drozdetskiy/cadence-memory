from __future__ import annotations

from cadence_memory.executor.events import (
    AssistantEvent,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ResultEvent,
    ResultPayload,
    TextContent,
    TextDelta,
    ToolUseBlock,
    parse_event,
)


def test_assistant_event_with_text_content() -> None:
    event = parse_event(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
    )
    assert isinstance(event, AssistantEvent)
    assert event.type == "assistant"
    assert event.message is not None
    assert event.message.content == [TextContent(text="hello")]


def test_content_block_delta_text_delta() -> None:
    event = parse_event(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": " world"},
        }
    )
    assert isinstance(event, ContentBlockDeltaEvent)
    assert event.delta == TextDelta(text=" world")


def test_content_block_start_tool_use() -> None:
    event = parse_event(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read"},
        }
    )
    assert isinstance(event, ContentBlockStartEvent)
    assert event.content_block == ToolUseBlock(name="Read")


def test_result_event_with_string_payload() -> None:
    event = parse_event({"type": "result", "result": "done"})
    assert isinstance(event, ResultEvent)
    assert event.result == "done"


def test_result_event_with_dict_output() -> None:
    event = parse_event({"type": "result", "result": {"output": "final"}})
    assert isinstance(event, ResultEvent)
    assert event.result == ResultPayload(output="final")


def test_unknown_event_type_returns_none() -> None:
    assert parse_event({"type": "system_unknown"}) is None


def test_non_dict_input_returns_none() -> None:
    assert parse_event("not a dict") is None
    assert parse_event(None) is None
    assert parse_event(42) is None
