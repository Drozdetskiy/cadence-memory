"""Tests for progress event dataclasses."""

from __future__ import annotations

import datetime

from cadence_memory.progress.events import (
    ClaudeProgressEvent,
    ErrorEvent,
    IngestEndEvent,
    IngestStartEvent,
    PhaseEndEvent,
    PhaseStartEvent,
    ProgressEvent,
    StageEndEvent,
    StageStartEvent,
    now_ts,
)


def test_now_ts_format() -> None:
    ts = now_ts()
    assert ts.endswith("Z")
    assert "." not in ts


def test_now_ts_roundtrip_utc() -> None:
    ts = now_ts()
    dt = datetime.datetime.fromisoformat(ts)
    assert dt.tzinfo is not None
    assert dt.utcoffset() == datetime.timedelta(0)


def test_phase_start_event_full() -> None:
    e = PhaseStartEvent(phase="bootstrap", repo="my/repo", stage="init", model="claude-3")
    d = e.to_jsonl_dict()
    assert d["event"] == "phase_start"
    assert d["phase"] == "bootstrap"
    assert d["repo"] == "my/repo"
    assert d["stage"] == "init"
    assert d["model"] == "claude-3"
    assert "ts" in d


def test_phase_start_event_defaults() -> None:
    e = PhaseStartEvent(phase="lint")
    d = e.to_jsonl_dict()
    assert d["event"] == "phase_start"
    assert d["repo"] is None
    assert d["stage"] is None
    assert d["model"] is None
    assert d["source"] is None


def test_phase_start_event_source_field() -> None:
    e = PhaseStartEvent(phase="manual-ingest", source="/abs/path/note.md")
    d = e.to_jsonl_dict()
    assert d["source"] == "/abs/path/note.md"
    assert d["stage"] is None


def test_phase_end_event_full() -> None:
    e = PhaseEndEvent(
        phase="bootstrap",
        duration_ms=5000,
        result="success",
        tokens_in_total=100,
        tokens_out_total=200,
        cost_usd_estimate=0.05,
    )
    d = e.to_jsonl_dict()
    assert d["event"] == "phase_end"
    assert d["phase"] == "bootstrap"
    assert d["duration_ms"] == 5000
    assert d["result"] == "success"
    assert d["tokens_in_total"] == 100
    assert d["tokens_out_total"] == 200
    assert d["cost_usd_estimate"] == 0.05
    assert "ts" in d


def test_phase_end_event_defaults() -> None:
    e = PhaseEndEvent(phase="ingest")
    d = e.to_jsonl_dict()
    assert d["duration_ms"] is None
    assert d["result"] is None
    assert d["cost_usd_estimate"] is None


def test_stage_start_event() -> None:
    e = StageStartEvent(stage_index=2, stage_name="page-map", repo="org/repo")
    d = e.to_jsonl_dict()
    assert d["event"] == "stage_start"
    assert d["stage_index"] == 2
    assert d["stage_name"] == "page-map"
    assert d["repo"] == "org/repo"
    assert "ts" in d


def test_stage_end_event_full() -> None:
    e = StageEndEvent(
        stage_index=3,
        duration_ms=3200,
        result="success",
        pages_touched=7,
        cost_usd_estimate=0.03,
    )
    d = e.to_jsonl_dict()
    assert d["event"] == "stage_end"
    assert d["stage_index"] == 3
    assert d["duration_ms"] == 3200
    assert d["result"] == "success"
    assert d["pages_touched"] == 7
    assert d["cost_usd_estimate"] == 0.03
    assert "ts" in d


def test_stage_end_event_defaults() -> None:
    e = StageEndEvent(stage_index=0)
    d = e.to_jsonl_dict()
    assert d["duration_ms"] is None
    assert d["pages_touched"] is None


def test_ingest_start_event() -> None:
    e = IngestStartEvent(repo="org/repo", commit_sha="abc1234", subject="fix: something")
    d = e.to_jsonl_dict()
    assert d["event"] == "ingest_start"
    assert d["repo"] == "org/repo"
    assert d["commit_sha"] == "abc1234"
    assert d["subject"] == "fix: something"
    assert "ts" in d


def test_ingest_end_event_full() -> None:
    e = IngestEndEvent(
        repo="org/repo",
        commit_sha="abc1234",
        duration_ms=2000,
        result="success",
        pages_touched=4,
        cost_usd_estimate=0.02,
    )
    d = e.to_jsonl_dict()
    assert d["event"] == "ingest_end"
    assert d["repo"] == "org/repo"
    assert d["commit_sha"] == "abc1234"
    assert d["duration_ms"] == 2000
    assert d["result"] == "success"
    assert d["pages_touched"] == 4
    assert d["cost_usd_estimate"] == 0.02
    assert "ts" in d


def test_ingest_end_event_defaults() -> None:
    e = IngestEndEvent(repo="org/repo", commit_sha="abc")
    d = e.to_jsonl_dict()
    assert d["duration_ms"] is None
    assert d["pages_touched"] is None
    assert d["result"] is None


def test_claude_progress_event_with_detail() -> None:
    e = ClaudeProgressEvent(phase="bootstrap-stage", kind="tool-call", detail="Read: src/foo.py")
    d = e.to_jsonl_dict()
    assert d["event"] == "claude_progress"
    assert d["phase"] == "bootstrap-stage"
    assert d["kind"] == "tool-call"
    assert d["detail"] == "Read: src/foo.py"
    assert "ts" in d


def test_claude_progress_event_no_detail() -> None:
    e = ClaudeProgressEvent(phase="claude", kind="signal")
    d = e.to_jsonl_dict()
    assert d["event"] == "claude_progress"
    assert d["detail"] is None


def test_error_event_with_detail() -> None:
    e = ErrorEvent(phase="bootstrap", message="stage failed", detail="exit code 1")
    d = e.to_jsonl_dict()
    assert d["event"] == "error"
    assert d["phase"] == "bootstrap"
    assert d["message"] == "stage failed"
    assert d["detail"] == "exit code 1"
    assert "ts" in d


def test_error_event_no_detail() -> None:
    e = ErrorEvent(phase="ingest", message="claude failed")
    d = e.to_jsonl_dict()
    assert d["detail"] is None


def test_all_events_have_ts_field() -> None:
    events: list[ProgressEvent] = [
        PhaseStartEvent(phase="bootstrap"),
        PhaseEndEvent(phase="bootstrap"),
        StageStartEvent(stage_index=0, stage_name="init", repo="x"),
        StageEndEvent(stage_index=0),
        IngestStartEvent(repo="x", commit_sha="abc", subject="fix"),
        IngestEndEvent(repo="x", commit_sha="abc"),
        ClaudeProgressEvent(phase="claude", kind="tool-call"),
        ErrorEvent(phase="ingest", message="fail"),
    ]
    assert len(events) == 8
    for ev in events:
        d = ev.to_jsonl_dict()
        ts = d["ts"]
        assert isinstance(ts, str)
        assert ts.endswith("Z")
        dt = datetime.datetime.fromisoformat(ts)
        assert dt.utcoffset() == datetime.timedelta(0)


def test_events_are_frozen() -> None:
    import pytest

    e = PhaseStartEvent(phase="bootstrap")
    with pytest.raises(AttributeError):
        e.phase = "lint"  # type: ignore[misc]
