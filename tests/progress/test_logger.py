"""Tests for progress Logger Protocol, StdoutLogger, and NullLogger."""

from __future__ import annotations

import io
import json
import pathlib
import sys

import pytest

from cadence_memory.progress.events import (
    ClaudeProgressEvent,
    ErrorEvent,
    IngestEndEvent,
    IngestStartEvent,
    PhaseEndEvent,
    PhaseStartEvent,
    StageEndEvent,
    StageStartEvent,
)
from cadence_memory.progress.logger import Logger, NullLogger, StdoutLogger, _format_event_summary

# --- format helpers ---


def test_format_phase_start_no_extras() -> None:
    e = PhaseStartEvent(phase="bootstrap")
    assert _format_event_summary(e) == "phase bootstrap starting"


def test_format_phase_start_with_repo_and_model() -> None:
    e = PhaseStartEvent(phase="bootstrap", repo="org/repo", model="claude-3")
    s = _format_event_summary(e)
    assert "phase bootstrap starting" in s
    assert "repo=org/repo" in s
    assert "model=claude-3" in s


def test_format_phase_end_full() -> None:
    e = PhaseEndEvent(phase="ingest", duration_ms=3000, result="ok", cost_usd_estimate=0.05)
    s = _format_event_summary(e)
    assert "phase ingest done" in s
    assert "3000ms" in s
    assert "result=ok" in s
    assert "$0.0500" in s


def test_format_phase_end_minimal() -> None:
    e = PhaseEndEvent(phase="lint")
    assert _format_event_summary(e) == "phase lint done"


def test_format_stage_start() -> None:
    e = StageStartEvent(stage_index=2, stage_name="page-map", repo="org/repo")
    assert _format_event_summary(e) == "stage 2: page-map (org/repo)"


def test_format_stage_end_full() -> None:
    e = StageEndEvent(stage_index=1, duration_ms=1500, result="ok", pages_touched=4)
    s = _format_event_summary(e)
    assert "stage 1 done" in s
    assert "1500ms" in s
    assert "result=ok" in s
    assert "pages=4" in s


def test_format_stage_end_minimal() -> None:
    e = StageEndEvent(stage_index=0)
    assert _format_event_summary(e) == "stage 0 done"


def test_format_ingest_start() -> None:
    e = IngestStartEvent(repo="org/repo", commit_sha="abc123", subject="fix: bug")
    assert _format_event_summary(e) == "ingest org/repo@abc123: fix: bug"


def test_format_ingest_end_full() -> None:
    e = IngestEndEvent(repo="org/repo", commit_sha="abc123", duration_ms=800, result="ok")
    s = _format_event_summary(e)
    assert "ingest org/repo@abc123 done" in s
    assert "800ms" in s
    assert "result=ok" in s


def test_format_ingest_end_minimal() -> None:
    e = IngestEndEvent(repo="org/repo", commit_sha="abc")
    assert _format_event_summary(e) == "ingest org/repo@abc done"


def test_format_claude_progress_with_detail() -> None:
    e = ClaudeProgressEvent(phase="bootstrap-stage", kind="tool-call", detail="Read: src/foo.py")
    s = _format_event_summary(e)
    assert s == "[bootstrap-stage] tool-call: Read: src/foo.py"


def test_format_claude_progress_no_detail() -> None:
    e = ClaudeProgressEvent(phase="claude", kind="signal")
    assert _format_event_summary(e) == "[claude] signal"


def test_format_error_with_detail() -> None:
    e = ErrorEvent(phase="bootstrap", message="stage failed", detail="exit 1")
    s = _format_event_summary(e)
    assert s == "error in bootstrap: stage failed (exit 1)"


def test_format_error_no_detail() -> None:
    e = ErrorEvent(phase="ingest", message="claude failed")
    assert _format_event_summary(e) == "error in ingest: claude failed"


# --- StdoutLogger: stdout format ---


def test_info_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.info("hello world")
    out = capsys.readouterr().out
    assert "[INFO] hello world" in out
    assert out.endswith("\n")


def test_info_includes_timestamp(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.info("msg")
    out = capsys.readouterr().out
    assert out.startswith("[")
    assert "T" in out  # ISO-8601 separator


def test_info_format_string_substitution(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.info("value=%s count=%d", "x", 3)
    out = capsys.readouterr().out
    assert "value=x count=3" in out


def test_print_writes_raw_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.print("raw line")
    out = capsys.readouterr().out
    assert out == "raw line\n"


# --- StdoutLogger: stderr routing ---


def test_warn_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.warn("watch out")
    out, err = capsys.readouterr()
    assert "watch out" not in out
    assert "watch out" in err
    assert "WARN" in err


def test_error_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.error("bad thing")
    out, err = capsys.readouterr()
    assert "bad thing" not in out
    assert "bad thing" in err
    assert "ERROR" in err


# --- StdoutLogger: level filtering ---


def test_level_warn_suppresses_info(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(level="warn", color="never")
    logger.info("should not appear")
    out, err = capsys.readouterr()
    assert "should not appear" not in out
    assert "should not appear" not in err


def test_level_info_shows_info(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(level="info", color="never")
    logger.info("should appear")
    out = capsys.readouterr().out
    assert "should appear" in out


def test_level_error_suppresses_warn(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(level="error", color="never")
    logger.warn("filtered out")
    out, err = capsys.readouterr()
    assert "filtered out" not in out
    assert "filtered out" not in err


def test_level_error_shows_error(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(level="error", color="never")
    logger.error("visible error")
    _, err = capsys.readouterr()
    assert "visible error" in err


def test_level_debug_shows_info(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(level="debug", color="never")
    logger.info("info at debug level")
    out = capsys.readouterr().out
    assert "info at debug level" in out


# --- StdoutLogger: ANSI color ---


def test_ansi_never_no_escape_codes(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.info("msg")
    out = capsys.readouterr().out
    assert "\033[" not in out


def test_ansi_always_has_escape_codes(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="always")
    logger.info("msg")
    out = capsys.readouterr().out
    assert "\033[" in out


def test_ansi_always_error_has_red(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="always")
    logger.error("err msg")
    _, err = capsys.readouterr()
    assert "\033[31m" in err


def test_ansi_always_warn_has_yellow(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="always")
    logger.warn("warn msg")
    _, err = capsys.readouterr()
    assert "\033[33m" in err


def test_ansi_auto_non_tty_no_escape_codes(capsys: pytest.CaptureFixture[str]) -> None:
    # pytest capsys captures to non-tty streams, so auto should produce no ANSI
    logger = StdoutLogger(color="auto")
    logger.info("plain")
    out = capsys.readouterr().out
    assert "\033[" not in out


def test_ansi_auto_isatty_true(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStdout(io.StringIO):
        def isatty(self) -> bool:
            return True

    fake = FakeStdout()
    monkeypatch.setattr(sys, "stdout", fake)
    logger = StdoutLogger(color="auto")
    logger.info("colored msg")
    out = fake.getvalue()
    assert "\033[" in out


# --- StdoutLogger: section ---


def test_section_format(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.section("my-label")
    out = capsys.readouterr().out
    assert "\n=== my-label ===\n" in out


# --- StdoutLogger: log_event ---


def test_log_event_phase_start_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.log_event(PhaseStartEvent(phase="bootstrap", repo="org/repo"))
    out, err = capsys.readouterr()
    assert "phase bootstrap starting" in out
    assert err == ""


def test_log_event_error_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.log_event(ErrorEvent(phase="ingest", message="something went wrong"))
    out, err = capsys.readouterr()
    assert "something went wrong" in err
    assert out == ""


def test_log_event_stage_start_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    logger = StdoutLogger(color="never")
    logger.log_event(StageStartEvent(stage_index=1, stage_name="init", repo="r"))
    out = capsys.readouterr().out
    assert "stage 1: init" in out


# --- StdoutLogger: JSONL sink ---


def test_jsonl_sink_writes_valid_json(tmp_path: pathlib.Path) -> None:
    jsonl_file = tmp_path / "progress.jsonl"
    logger = StdoutLogger(jsonl_path=str(jsonl_file))
    logger.log_event(PhaseStartEvent(phase="bootstrap"))
    logger.log_event(PhaseEndEvent(phase="bootstrap", result="ok"))
    lines = jsonl_file.read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert "ts" in obj
        assert "event" in obj


def test_jsonl_sink_events_match_type(tmp_path: pathlib.Path) -> None:
    jsonl_file = tmp_path / "out.jsonl"
    logger = StdoutLogger(jsonl_path=str(jsonl_file))
    logger.log_event(IngestStartEvent(repo="r", commit_sha="abc", subject="fix"))
    logger.log_event(IngestEndEvent(repo="r", commit_sha="abc", result="ok"))
    lines = jsonl_file.read_text().splitlines()
    assert json.loads(lines[0])["event"] == "ingest_start"
    assert json.loads(lines[1])["event"] == "ingest_end"


def test_jsonl_records_regardless_of_level(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jsonl_file = tmp_path / "out.jsonl"
    logger = StdoutLogger(level="error", color="never", jsonl_path=str(jsonl_file))
    logger.log_event(PhaseStartEvent(phase="lint"))
    out = capsys.readouterr().out
    assert out == ""  # info suppressed from stdout
    assert jsonl_file.exists()
    obj = json.loads(jsonl_file.read_text().strip())
    assert obj["event"] == "phase_start"


def test_jsonl_creates_parent_dirs(tmp_path: pathlib.Path) -> None:
    jsonl_file = tmp_path / "nested" / "dir" / "progress.jsonl"
    logger = StdoutLogger(jsonl_path=str(jsonl_file))
    logger.log_event(StageEndEvent(stage_index=0))
    assert jsonl_file.exists()


def test_jsonl_appends_across_events(tmp_path: pathlib.Path) -> None:
    jsonl_file = tmp_path / "out.jsonl"
    logger = StdoutLogger(jsonl_path=str(jsonl_file))
    for i in range(3):
        logger.log_event(StageStartEvent(stage_index=i, stage_name="s", repo="r"))
    assert len(jsonl_file.read_text().splitlines()) == 3


def test_path_property_returns_jsonl_path(tmp_path: pathlib.Path) -> None:
    p = str(tmp_path / "out.jsonl")
    logger = StdoutLogger(jsonl_path=p)
    assert logger.path == p


def test_path_property_none_when_no_jsonl() -> None:
    logger = StdoutLogger()
    assert logger.path is None


# --- NullLogger ---


def test_null_logger_is_noop(capsys: pytest.CaptureFixture[str]) -> None:
    logger = NullLogger()
    logger.print("something")
    logger.info("something")
    logger.warn("something")
    logger.error("something")
    logger.section("label")
    logger.log_event(PhaseStartEvent(phase="bootstrap"))
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""


def test_null_logger_path_is_none() -> None:
    assert NullLogger().path is None


# --- Protocol structural check ---


def test_stdout_logger_satisfies_logger_protocol() -> None:
    logger: Logger = StdoutLogger()
    assert logger.path is None


def test_null_logger_satisfies_logger_protocol() -> None:
    logger: Logger = NullLogger()
    assert logger.path is None
