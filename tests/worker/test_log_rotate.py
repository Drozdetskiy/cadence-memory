"""Tests for log_rotate parser, grouper, and planner - Tasks 1-3 (design2 §6.1)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import UTC, date
from datetime import datetime as dt
from pathlib import Path

import pytest

import cadence_memory.worker.log_rotate as lr
from cadence_memory.worker.log_rotate import (
    _ENTRY_HEADER_RE,
    LogEntry,
    LogRotationDuplicateError,
    LogRotationError,
    _group_by_month,
    _parse_log_entries,
    _plan_rotation,
    _split_frontmatter,
    rotate_log,
)
from cadence_memory.worker.wiki_commit import WikiCommitError

_FRONTMATTER = (
    "---\n"
    'title: "Activity Log"\n'
    "type: log\n"
    "project: _master\n"
    "created: 2026-05-12\n"
    "updated: 2026-05-12\n"
    "tags: []\n"
    "confidence: high\n"
    "---\n"
    "\n"
    "**TLDR**: Append-only chronological audit.\n"
    "\n"
)

_ENTRY_MAY_01 = "## [2026-05-01] init | first entry\n\nFirst entry body.\n\n"
_ENTRY_MAY_12 = "## [2026-05-12] patch | second entry\n\nSecond entry body.\n\n"
_ENTRY_MAY_14 = "## [2026-05-14] patch | third entry\n\nThird entry body.\n"


def test_split_frontmatter_with_fence() -> None:
    text = _FRONTMATTER + _ENTRY_MAY_01 + _ENTRY_MAY_12
    fm, body = _split_frontmatter(text)
    assert fm == _FRONTMATTER
    assert body.startswith("## [2026-05-01]")
    assert "**TLDR**" not in body


def test_split_frontmatter_no_fence() -> None:
    text = _ENTRY_MAY_01 + _ENTRY_MAY_12
    fm, body = _split_frontmatter(text)
    assert fm == ""
    assert body == text


def test_parse_entries_single_month() -> None:
    body = _ENTRY_MAY_01 + _ENTRY_MAY_12 + _ENTRY_MAY_14
    entries = _parse_log_entries(body)
    assert len(entries) == 3
    assert entries[0].date_iso == "2026-05-01"
    assert entries[0].year_month == "2026-05"
    assert entries[0].text.startswith("## [2026-05-01]")
    assert entries[1].date_iso == "2026-05-12"
    assert entries[2].date_iso == "2026-05-14"


def test_parse_entries_preserves_multiline_body() -> None:
    multiline = (
        "## [2026-05-01] update | big change\n"
        "\n"
        "Paragraph about the change.\n"
        "\n"
        "```python\n"
        "x = 1\n"
        "```\n"
        "\n"
        "Another paragraph.\n"
        "\n"
    )
    next_entry = "## [2026-05-02] fix | follow-up\n\nFollow-up.\n"
    entries = _parse_log_entries(multiline + next_entry)
    assert len(entries) == 2
    assert "```python" in entries[0].text
    assert "Another paragraph." in entries[0].text
    assert entries[0].text.endswith("\n\n")


def test_parse_entries_handles_no_entries() -> None:
    body = "**TLDR**: only a tldr, no entries yet.\n"
    entries = _parse_log_entries(body)
    assert entries == []


def test_parse_entries_same_date_multiple_entries() -> None:
    body = (
        "## [2026-05-12] fix | first fix\n\nFirst fix.\n\n"
        "## [2026-05-12] fix | second fix\n\nSecond fix.\n"
    )
    entries = _parse_log_entries(body)
    assert len(entries) == 2
    assert entries[0].date_iso == "2026-05-12"
    assert entries[1].date_iso == "2026-05-12"
    assert "first fix" in entries[0].text
    assert "second fix" in entries[1].text


def test_group_by_month_insertion_order() -> None:
    entries = [
        LogEntry(date_iso="2026-03-15", year_month="2026-03", text="## [2026-03-15] a\n"),
        LogEntry(date_iso="2026-05-01", year_month="2026-05", text="## [2026-05-01] b\n"),
        LogEntry(date_iso="2026-04-10", year_month="2026-04", text="## [2026-04-10] c\n"),
    ]
    groups = _group_by_month(entries)
    keys = list(groups.keys())
    assert keys == ["2026-03", "2026-05", "2026-04"]
    assert len(groups["2026-03"]) == 1
    assert len(groups["2026-05"]) == 1
    assert len(groups["2026-04"]) == 1


# ---------------------------------------------------------------------------
# Task 2: _plan_rotation tests
# ---------------------------------------------------------------------------


def _make_entries(year_month: str, count: int) -> list[LogEntry]:
    """Produce `count` synthetic LogEntry objects for `year_month` (YYYY-MM)."""
    result: list[LogEntry] = []
    for i in range(count):
        day = (i % 28) + 1
        date_iso = f"{year_month}-{day:02d}"
        result.append(
            LogEntry(date_iso=date_iso, year_month=year_month, text=f"## [{date_iso}] entry {i}\n")
        )
    return result


def test_plan_under_threshold() -> None:
    entries = (
        _make_entries("2026-03", 17) + _make_entries("2026-04", 16) + _make_entries("2026-05", 17)
    )
    plan = _plan_rotation(entries, date(2026, 5, 14))
    assert not plan.should_rotate
    assert "threshold" in plan.reason
    assert "50" in plan.reason


def test_plan_only_current_month() -> None:
    entries = _make_entries("2026-05", 150)
    plan = _plan_rotation(entries, date(2026, 5, 14))
    assert not plan.should_rotate
    assert "current or previous month" in plan.reason


def test_plan_only_current_and_previous() -> None:
    entries = _make_entries("2026-04", 75) + _make_entries("2026-05", 75)
    plan = _plan_rotation(entries, date(2026, 5, 14))
    assert not plan.should_rotate
    assert "current or previous month" in plan.reason


def test_plan_three_months_above_threshold() -> None:
    entries = (
        _make_entries("2026-03", 50) + _make_entries("2026-04", 50) + _make_entries("2026-05", 100)
    )
    plan = _plan_rotation(entries, date(2026, 5, 14))
    assert plan.should_rotate
    assert plan.oldest_archived_month == "2026-03"
    assert "2026-03" in plan.to_archive
    assert len(plan.to_archive["2026-03"]) == 50
    assert len(plan.retained) == 150


def test_plan_january_rollover() -> None:
    entries = _make_entries("2025-12", 50) + _make_entries("2026-01", 100)
    plan = _plan_rotation(entries, date(2026, 2, 3))
    assert plan.should_rotate
    assert plan.oldest_archived_month == "2025-12"
    assert "2025-12" in plan.to_archive
    assert len(plan.to_archive["2025-12"]) == 50
    assert len(plan.retained) == 100


def test_plan_retention_floor_pulls_back() -> None:
    entries = _make_entries("2026-03", 200)
    plan = _plan_rotation(entries, date(2026, 5, 14))
    assert plan.should_rotate
    assert plan.oldest_archived_month == "2026-03"
    assert len(plan.retained) == lr.RETAIN_MIN
    assert len(plan.to_archive["2026-03"]) == 200 - lr.RETAIN_MIN


def test_plan_retention_floor_absorbs_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lr, "ROTATE_THRESHOLD", 10)
    entries = _make_entries("2026-03", 25)
    plan = _plan_rotation(entries, date(2026, 5, 14))
    assert not plan.should_rotate
    assert "absorbed" in plan.reason


# ---------------------------------------------------------------------------
# Task 3: Integration tests -- disk writes + git commit + rotate_log
# ---------------------------------------------------------------------------

_LOG_FM = (
    "---\n"
    'title: "Activity Log"\n'
    "type: log\n"
    "project: _master\n"
    "created: 2026-05-12\n"
    "updated: 2026-05-12\n"
    "tags: []\n"
    "confidence: high\n"
    "---\n"
    "\n"
)


def _git_cmd(wiki: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=wiki,
        check=True,
        text=True,
        capture_output=True,
    )


def _init_wiki_fs(tmp_path: Path, log_content: str) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git_cmd(wiki, "init", "--initial-branch=main")
    _git_cmd(wiki, "config", "user.email", "test@example.com")
    _git_cmd(wiki, "config", "user.name", "Test User")
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    (wiki / "log.md").write_text(log_content, encoding="utf-8")
    _git_cmd(wiki, "add", "-A")
    _git_cmd(wiki, "commit", "-m", "seed")
    return wiki


def _make_log_content(frontmatter: str, months_entries: dict[str, int]) -> str:
    parts: list[str] = []
    for ym, count in months_entries.items():
        for i in range(count):
            day = (i % 28) + 1
            date_iso = f"{ym}-{day:02d}"
            parts.append(f"## [{date_iso}] entry {i}\n\nBody {i}.\n\n")
    return frontmatter + "".join(parts)


def _clock_fixed(year: int, month: int, day: int) -> Callable[[], dt]:
    def _inner() -> dt:
        return dt(year, month, day, tzinfo=UTC)

    return _inner


def test_rotate_no_op_when_log_md_missing(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git_cmd(wiki, "init", "--initial-branch=main")
    _git_cmd(wiki, "config", "user.email", "test@example.com")
    _git_cmd(wiki, "config", "user.name", "Test User")
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    _git_cmd(wiki, "add", "-A")
    _git_cmd(wiki, "commit", "-m", "seed")
    outcome = rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    assert not outcome.rotated
    assert outcome.archived_entries == 0
    assert outcome.archive_files == ()
    assert outcome.retained_entries == 0
    assert outcome.commit_sha is None
    assert outcome.error is None
    assert not (wiki / "log").exists()


def test_rotate_multi_month_duplicate_on_second_month_does_not_corrupt(tmp_path: Path) -> None:
    # 2026-02 entries: day 1..28 (no day 15 collision)
    feb_body = "".join(
        f"## [2026-02-{(i % 28) + 1:02d}] entry feb-{i}\n\nBody feb-{i}.\n\n" for i in range(50)
    )
    # 2026-03 entries: include day 15 to collide with the pre-seeded archive
    mar_body = "".join(
        f"## [2026-03-{15 + i:02d}] entry mar-{i}\n\nBody mar-{i}.\n\n" for i in range(5)
    )
    mar_body += "".join(
        f"## [2026-03-{i:02d}] entry mar-pad-{i}\n\nBody.\n\n" for i in range(1, 46)
    )
    apr_body = "".join(
        f"## [2026-04-{(i % 28) + 1:02d}] entry apr-{i}\n\nBody apr-{i}.\n\n" for i in range(60)
    )
    may_body = "".join(
        f"## [2026-05-{(i % 28) + 1:02d}] entry may-{i}\n\nBody may-{i}.\n\n" for i in range(60)
    )
    wiki = _init_wiki_fs(tmp_path, _LOG_FM + feb_body + mar_body + apr_body + may_body)
    log_md_before = (wiki / "log.md").read_text(encoding="utf-8")
    head_sha_before = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()

    archive_dir = wiki / "log"
    archive_dir.mkdir()
    pre_archive = (
        "---\n"
        'title: "Log archive — 2026-03"\n'
        "type: log\n"
        "project: _master\n"
        "created: 2026-03-01\n"
        "updated: 2026-03-01\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
        "\n"
        "## [2026-03-15] existing entry\n\nExisting body.\n\n"
    )
    pre_archive_path = archive_dir / "2026-03.md"
    pre_archive_path.write_text(pre_archive, encoding="utf-8")
    _git_cmd(wiki, "add", "-A")
    _git_cmd(wiki, "commit", "-m", "seed 2026-03 archive")
    head_sha_with_archive = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(LogRotationDuplicateError):
        rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))

    # log.md must not be rewritten when rotation aborts
    assert (wiki / "log.md").read_text(encoding="utf-8") == log_md_before
    # 2026-02 archive must NOT have been written (no partial archive on disk)
    assert not (archive_dir / "2026-02.md").exists()
    # Pre-seeded 2026-03 archive must be untouched
    assert pre_archive_path.read_text(encoding="utf-8") == pre_archive
    # No new commit created
    head_sha_after = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()
    assert head_sha_after == head_sha_with_archive
    assert head_sha_after != head_sha_before


def test_rotate_no_op_when_under_threshold(tmp_path: Path) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 3, "2026-04": 1, "2026-05": 1})
    wiki = _init_wiki_fs(tmp_path, content)
    outcome = rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    assert not outcome.rotated
    assert not (wiki / "log").exists()
    result = _git_cmd(wiki, "log", "--oneline")
    assert len(result.stdout.strip().splitlines()) == 1


def test_rotate_archives_oldest_month(tmp_path: Path) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 50, "2026-04": 50, "2026-05": 100})
    wiki = _init_wiki_fs(tmp_path, content)
    outcome = rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    assert outcome.rotated
    assert outcome.archived_entries == 50
    assert outcome.retained_entries == 150
    archive = wiki / "log" / "2026-03.md"
    assert archive.exists()
    archive_text = archive.read_text(encoding="utf-8")
    assert 'title: "Log archive — 2026-03"' in archive_text
    assert len(_ENTRY_HEADER_RE.findall(archive_text)) == 50
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert len(_ENTRY_HEADER_RE.findall(log_text)) == 150
    log_result = _git_cmd(wiki, "log", "--oneline", "-2")
    assert "chore: rotate log.md (2026-03 archive)" in log_result.stdout


def test_rotate_preserves_frontmatter(tmp_path: Path) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 50, "2026-04": 50, "2026-05": 100})
    wiki = _init_wiki_fs(tmp_path, content)
    original = (wiki / "log.md").read_text(encoding="utf-8")
    original_fm = original[: original.index("## [")]
    rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    after = (wiki / "log.md").read_text(encoding="utf-8")
    after_fm = after[: after.index("## [")]
    assert original_fm == after_fm


def test_rotate_dry_run_writes_nothing(tmp_path: Path) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 50, "2026-04": 50, "2026-05": 100})
    wiki = _init_wiki_fs(tmp_path, content)
    mtime_before = os.path.getmtime(str(wiki / "log.md"))
    outcome = rotate_log(wiki_dir=wiki, dry_run=True, clock=_clock_fixed(2026, 5, 14))
    assert not outcome.rotated
    assert outcome.archived_entries == 50
    assert len(outcome.archive_files) == 1
    assert not (wiki / "log").exists()
    assert os.path.getmtime(str(wiki / "log.md")) == mtime_before


def test_rotate_appends_to_existing_archive(tmp_path: Path) -> None:
    # 2026-03 entries start on day 15, avoiding the pre-seeded 2026-03-01
    mar_body = "".join(f"## [2026-03-{15 + i:02d}] entry {i}\n\nBody {i}.\n\n" for i in range(5))
    apr_body = "".join(
        f"## [2026-04-{(i % 28) + 1:02d}] entry {i}\n\nBody {i}.\n\n" for i in range(60)
    )
    may_body = "".join(
        f"## [2026-05-{(i % 28) + 1:02d}] entry {i}\n\nBody {i}.\n\n" for i in range(60)
    )
    wiki = _init_wiki_fs(tmp_path, _LOG_FM + mar_body + apr_body + may_body)
    archive_dir = wiki / "log"
    archive_dir.mkdir()
    pre_archive = (
        "---\n"
        'title: "Log archive — 2026-03"\n'
        "type: log\n"
        "project: _master\n"
        "created: 2026-03-01\n"
        "updated: 2026-03-01\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
        "\n"
        "## [2026-03-01] old entry\n\nOld body.\n\n"
    )
    (archive_dir / "2026-03.md").write_text(pre_archive, encoding="utf-8")
    _git_cmd(wiki, "add", "-A")
    _git_cmd(wiki, "commit", "-m", "seed archive")
    outcome = rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    assert outcome.rotated
    archive_text = (wiki / "log" / "2026-03.md").read_text(encoding="utf-8")
    assert "## [2026-03-01]" in archive_text
    assert "## [2026-03-15]" in archive_text


def test_rotate_duplicate_archive_entry_raises(tmp_path: Path) -> None:
    # 2026-03 entries include day 1 (i=0 -> day=1 -> "2026-03-01")
    mar_body = "".join(
        f"## [2026-03-{(i % 28) + 1:02d}] entry {i}\n\nBody {i}.\n\n" for i in range(10)
    )
    apr_body = "".join(
        f"## [2026-04-{(i % 28) + 1:02d}] entry {i}\n\nBody {i}.\n\n" for i in range(60)
    )
    may_body = "".join(
        f"## [2026-05-{(i % 28) + 1:02d}] entry {i}\n\nBody {i}.\n\n" for i in range(60)
    )
    wiki = _init_wiki_fs(tmp_path, _LOG_FM + mar_body + apr_body + may_body)
    archive_dir = wiki / "log"
    archive_dir.mkdir()
    pre_archive = (
        "---\n"
        'title: "Log archive — 2026-03"\n'
        "type: log\n"
        "project: _master\n"
        "created: 2026-03-01\n"
        "updated: 2026-03-01\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
        "\n"
        "## [2026-03-01] existing entry\n\nExisting body.\n\n"
    )
    (archive_dir / "2026-03.md").write_text(pre_archive, encoding="utf-8")
    _git_cmd(wiki, "add", "-A")
    _git_cmd(wiki, "commit", "-m", "seed archive")
    with pytest.raises(LogRotationDuplicateError):
        rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))


def test_rotate_idempotent_within_month(tmp_path: Path) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 50, "2026-04": 50, "2026-05": 100})
    wiki = _init_wiki_fs(tmp_path, content)
    clock = _clock_fixed(2026, 5, 14)
    outcome1 = rotate_log(wiki_dir=wiki, clock=clock)
    assert outcome1.rotated
    outcome2 = rotate_log(wiki_dir=wiki, clock=clock)
    assert not outcome2.rotated
    log_result = _git_cmd(wiki, "log", "--oneline")
    assert len(log_result.stdout.strip().splitlines()) == 2


def test_rotate_commit_message_uses_oldest_month(tmp_path: Path) -> None:
    content = _make_log_content(
        _LOG_FM,
        {"2026-02": 50, "2026-03": 50, "2026-04": 50, "2026-05": 60},
    )
    wiki = _init_wiki_fs(tmp_path, content)
    outcome = rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    assert outcome.rotated
    log_result = _git_cmd(wiki, "log", "--oneline", "-1")
    assert "2026-02" in log_result.stdout
    assert (wiki / "log" / "2026-02.md").exists()
    assert (wiki / "log" / "2026-03.md").exists()


def test_rotate_commits_only_log_paths(tmp_path: Path) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 50, "2026-04": 50, "2026-05": 100})
    wiki = _init_wiki_fs(tmp_path, content)
    (wiki / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    diff_result = _git_cmd(wiki, "diff", "--name-only", "HEAD~1", "HEAD")
    changed_files = diff_result.stdout.strip().splitlines()
    assert "unrelated.txt" not in changed_files
    assert "log.md" in changed_files
    assert "log/2026-03.md" in changed_files
    status = _git_cmd(wiki, "status", "--porcelain")
    assert "unrelated.txt" in status.stdout


def test_rotate_log_tail_compat_after_rotation(tmp_path: Path) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 200})
    wiki = _init_wiki_fs(tmp_path, content)
    rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    from cadence_memory.worker.ingest import _log_tail

    tail = _log_tail(wiki)
    entry_headers = [line for line in tail.splitlines() if line.startswith("## ")]
    assert len(entry_headers) == 15


def test_rotate_commit_failure_rolls_back_disk_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 50, "2026-04": 50, "2026-05": 100})
    wiki = _init_wiki_fs(tmp_path, content)
    log_md_before = (wiki / "log.md").read_text(encoding="utf-8")
    head_before = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()

    real_run_checked = lr._run_checked
    call_count = {"n": 0}

    def fake_run_checked(argv: list[str], *, cwd: Path) -> object:
        if len(argv) >= 2 and argv[1] == "commit":
            call_count["n"] += 1
            raise WikiCommitError("simulated commit failure")
        return real_run_checked(argv, cwd=cwd)

    monkeypatch.setattr(lr, "_run_checked", fake_run_checked)

    with pytest.raises(LogRotationError):
        rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))

    assert call_count["n"] == 1
    # log.md restored to pre-rotation content
    assert (wiki / "log.md").read_text(encoding="utf-8") == log_md_before
    # Archive file deleted (it did not exist before the failed rotation)
    assert not (wiki / "log" / "2026-03.md").exists()
    # No new commit recorded
    head_after = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before


def test_rotate_write_oserror_rolls_back_disk_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _make_log_content(
        _LOG_FM, {"2026-02": 50, "2026-03": 50, "2026-04": 50, "2026-05": 60}
    )
    wiki = _init_wiki_fs(tmp_path, content)
    log_md_before = (wiki / "log.md").read_text(encoding="utf-8")
    head_before = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()

    real_append = lr._append_to_archive
    call_count = {"n": 0}

    def fake_append(path: Path, entries: object, today_iso: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated disk full")
        real_append(path, entries, today_iso)  # type: ignore[arg-type]

    monkeypatch.setattr(lr, "_append_to_archive", fake_append)

    with pytest.raises(LogRotationError):
        rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))

    # First archive write succeeded then was rolled back, second never wrote.
    assert not (wiki / "log" / "2026-02.md").exists()
    assert not (wiki / "log" / "2026-03.md").exists()
    # log.md untouched (never reached the rewrite step, but rollback is a no-op there).
    assert (wiki / "log.md").read_text(encoding="utf-8") == log_md_before
    # No new commit recorded.
    head_after = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before


def test_rotate_preflight_oserror_raises_log_rotation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 50, "2026-04": 50, "2026-05": 100})
    wiki = _init_wiki_fs(tmp_path, content)
    log_md_before = (wiki / "log.md").read_text(encoding="utf-8")
    head_before = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()

    real_mkdir = Path.mkdir

    def fake_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == lr.ARCHIVE_DIR and self.parent == wiki:
            raise OSError("simulated permission denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    with pytest.raises(LogRotationError):
        rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))

    assert (wiki / "log.md").read_text(encoding="utf-8") == log_md_before
    assert not (wiki / "log" / "2026-03.md").exists()
    head_after = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before


def test_rotate_initial_read_oserror_raises_log_rotation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 50, "2026-04": 50, "2026-05": 60})
    wiki = _init_wiki_fs(tmp_path, content)
    head_before = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()

    real_read = Path.read_text
    log_md = wiki / "log.md"

    def fake_read(self: Path, *args: object, **kwargs: object) -> str:
        if self == log_md:
            raise OSError("simulated read failure")
        return real_read(self, *args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr(Path, "read_text", fake_read)

    with pytest.raises(LogRotationError):
        rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))

    head_after = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before
    assert not (wiki / "log").exists()


def test_rotate_rewrite_oserror_rolls_back_disk_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _make_log_content(
        _LOG_FM, {"2026-02": 50, "2026-03": 50, "2026-04": 50, "2026-05": 60}
    )
    wiki = _init_wiki_fs(tmp_path, content)
    log_md_before = (wiki / "log.md").read_text(encoding="utf-8")
    head_before = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()

    def fake_rewrite(path: Path, frontmatter: str, retained: object) -> None:
        raise OSError("simulated disk full on rewrite")

    monkeypatch.setattr(lr, "_rewrite_log_md", fake_rewrite)

    with pytest.raises(LogRotationError):
        rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))

    # All archive writes succeeded then were rolled back.
    assert not (wiki / "log" / "2026-02.md").exists()
    assert not (wiki / "log" / "2026-03.md").exists()
    # log.md unchanged (never rewritten; rollback restored snapshot).
    assert (wiki / "log.md").read_text(encoding="utf-8") == log_md_before
    head_after = _git_cmd(wiki, "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before


def test_append_log_failure_after_rotation(tmp_path: Path) -> None:
    content = _make_log_content(_LOG_FM, {"2026-03": 200})
    wiki = _init_wiki_fs(tmp_path, content)
    rotate_log(wiki_dir=wiki, clock=_clock_fixed(2026, 5, 14))
    from cadence_memory.worker.wiki_commit import append_log_failure

    append_log_failure(
        wiki_dir=wiki,
        repo_name="test-repo",
        short_sha="abc1234",
        subject="test subject",
        error="test error",
        today_iso="2026-05-14",
    )
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "## [2026-05-14] FAILED ingest | test-repo abc1234 — test subject" in log_text
    assert "test error" in log_text
    log_result = _git_cmd(wiki, "log", "--oneline", "-1")
    assert "ingest failure" in log_result.stdout
