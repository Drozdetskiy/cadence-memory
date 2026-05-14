"""Log rotation for log.md — parse, group, plan, write, commit (design2 §6.1)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cadence_memory.progress.logger import Logger, NullLogger
from cadence_memory.worker.wiki_commit import WikiCommitError, _run_checked

RETAIN_MIN: int = 30
ROTATE_THRESHOLD: int = 100
ARCHIVE_DIR: str = "log"

_ENTRY_HEADER_RE: re.Pattern[str] = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] ", re.MULTILINE)
_YEAR_MONTH_FMT: str = "%Y-%m"


class LogRotationError(Exception):
    """Raised when log rotation fails unexpectedly (wraps git and I/O errors)."""


class LogRotationDuplicateError(LogRotationError):
    """Raised when an incoming archive entry matches an existing dated header."""


@dataclass(frozen=True, slots=True)
class LogEntry:
    date_iso: str
    year_month: str
    text: str


@dataclass(frozen=True, slots=True)
class RotateOutcome:
    rotated: bool
    archived_entries: int
    archive_files: tuple[Path, ...]
    retained_entries: int
    commit_sha: str | None
    error: str | None


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return ("", text)
    first_entry = _ENTRY_HEADER_RE.search(text)
    if first_entry is None:
        return (text, "")
    split_pos = first_entry.start()
    return (text[:split_pos], text[split_pos:])


def _parse_log_entries(body: str) -> list[LogEntry]:
    matches = list(_ENTRY_HEADER_RE.finditer(body))
    if not matches:
        return []
    entries: list[LogEntry] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[start:end]
        date_iso = match.group(1)
        year_month = date_iso[:7]
        entries.append(LogEntry(date_iso=date_iso, year_month=year_month, text=text))
    return entries


def _group_by_month(entries: list[LogEntry]) -> dict[str, list[LogEntry]]:
    groups: dict[str, list[LogEntry]] = {}
    for entry in entries:
        if entry.year_month not in groups:
            groups[entry.year_month] = []
        groups[entry.year_month].append(entry)
    return groups


@dataclass(frozen=True, slots=True)
class RotationPlan:
    should_rotate: bool
    retained: tuple[LogEntry, ...]
    to_archive: dict[str, tuple[LogEntry, ...]]
    oldest_archived_month: str | None
    reason: str


def _plan_rotation(entries: list[LogEntry], today: date) -> RotationPlan:
    if len(entries) <= ROTATE_THRESHOLD:
        return RotationPlan(
            should_rotate=False,
            retained=tuple(entries),
            to_archive={},
            oldest_archived_month=None,
            reason=f"under threshold ({len(entries)} entries)",
        )

    current_ym = today.strftime(_YEAR_MONTH_FMT)
    prev_last = date(today.year, today.month, 1) - timedelta(days=1)
    previous_ym = prev_last.strftime(_YEAR_MONTH_FMT)
    keep_months = {current_ym, previous_ym}

    if not any(e.year_month < previous_ym for e in entries):
        return RotationPlan(
            should_rotate=False,
            retained=tuple(entries),
            to_archive={},
            oldest_archived_month=None,
            reason="all entries within current or previous month",
        )

    to_archive_ids: set[int] = {id(e) for e in entries if e.year_month not in keep_months}
    retained_count = len(entries) - len(to_archive_ids)

    if retained_count < RETAIN_MIN:
        needed = RETAIN_MIN - retained_count
        archived_sorted = sorted(
            (e for e in entries if id(e) in to_archive_ids),
            key=lambda e: (e.year_month, e.date_iso),
            reverse=True,
        )
        for e in archived_sorted:
            if needed <= 0:
                break
            to_archive_ids.discard(id(e))
            needed -= 1

    archived_groups: dict[str, list[LogEntry]] = {}
    for e in entries:
        if id(e) in to_archive_ids:
            if e.year_month not in archived_groups:
                archived_groups[e.year_month] = []
            archived_groups[e.year_month].append(e)

    if not archived_groups:
        return RotationPlan(
            should_rotate=False,
            retained=tuple(entries),
            to_archive={},
            oldest_archived_month=None,
            reason="retention floor absorbed all archivable entries",
        )

    to_archive = {ym: tuple(v) for ym, v in archived_groups.items()}
    oldest_archived_month = min(to_archive.keys())
    retained = tuple(e for e in entries if id(e) not in to_archive_ids)

    return RotationPlan(
        should_rotate=True,
        retained=retained,
        to_archive=to_archive,
        oldest_archived_month=oldest_archived_month,
        reason=f"archiving {len(to_archive)} month(s); oldest is {oldest_archived_month}",
    )


def _render_archive(year_month: str, entries: tuple[LogEntry, ...], today_iso: str) -> str:
    frontmatter = (
        "---\n"
        f'title: "Log archive — {year_month}"\n'
        "type: log\n"
        "project: _master\n"
        f"created: {today_iso}\n"
        f"updated: {today_iso}\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
        "\n"
    )
    body = "".join(e.text for e in entries)
    if body and not body.endswith("\n"):
        body += "\n"
    return frontmatter + body


def _check_archive_for_duplicates(path: Path, entries: tuple[LogEntry, ...]) -> None:
    if not path.exists():
        return
    existing = path.read_text(encoding="utf-8")
    existing_dates: set[str] = set()
    for line in existing.splitlines():
        m = _ENTRY_HEADER_RE.match(line)
        if m:
            existing_dates.add(m.group(1))
    for entry in entries:
        if entry.date_iso in existing_dates:
            raise LogRotationDuplicateError(
                f"duplicate archive entry: ## [{entry.date_iso}] already in {path}"
            )


def _append_to_archive(path: Path, entries: tuple[LogEntry, ...], today_iso: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_archive(path.stem, entries, today_iso), encoding="utf-8")
        return
    existing = path.read_text(encoding="utf-8")
    appended = "".join(e.text for e in entries)
    if existing and not existing.endswith("\n"):
        appended = "\n" + appended
    path.write_text(existing + appended, encoding="utf-8")


def _rewrite_log_md(path: Path, frontmatter: str, retained: tuple[LogEntry, ...]) -> None:
    body = "".join(e.text for e in retained)
    content = frontmatter + body
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _commit_rotation(wiki_dir: Path, touched: list[Path], oldest_ym: str) -> str:
    rel_paths = [str(p.relative_to(wiki_dir)) for p in touched]
    _run_checked(["git", "add", "--", *rel_paths], cwd=wiki_dir)
    _run_checked(
        ["git", "commit", "-m", f"chore: rotate log.md ({oldest_ym} archive)"],
        cwd=wiki_dir,
    )
    head = _run_checked(["git", "rev-parse", "HEAD"], cwd=wiki_dir)
    return head.stdout.strip()


def _utc_now() -> datetime:
    return datetime.now(UTC)


_NULL_LOGGER: Logger = NullLogger()


def rotate_log(
    *,
    wiki_dir: Path,
    dry_run: bool = False,
    logger: Logger = _NULL_LOGGER,
    clock: Callable[[], datetime] = _utc_now,
) -> RotateOutcome:
    log_path = wiki_dir / "log.md"
    if not log_path.is_file():
        logger.info("log rotation skipped: log.md not found")
        return RotateOutcome(
            rotated=False,
            archived_entries=0,
            archive_files=(),
            retained_entries=0,
            commit_sha=None,
            error=None,
        )

    today_dt = clock()
    today_date = today_dt.date()
    today_iso = today_date.isoformat()

    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogRotationError(f"failed to read log.md: {exc}") from exc
    frontmatter, body = _split_frontmatter(text)
    entries = _parse_log_entries(body)
    plan = _plan_rotation(entries, today_date)

    if not plan.should_rotate:
        logger.info("log rotation skipped: %s", plan.reason)
        return RotateOutcome(
            rotated=False,
            archived_entries=0,
            archive_files=(),
            retained_entries=len(entries),
            commit_sha=None,
            error=None,
        )

    archive_dir = wiki_dir / ARCHIVE_DIR
    total_archived = sum(len(v) for v in plan.to_archive.values())
    archive_paths = tuple(archive_dir / f"{ym}.md" for ym in plan.to_archive)

    if dry_run:
        for ym, ym_entries in plan.to_archive.items():
            logger.info(
                "planned: %s -> %d entries would archive to log/%s.md",
                ym,
                len(ym_entries),
                ym,
            )
        logger.info("planned: %d entries would be retained in log.md", len(plan.retained))
        return RotateOutcome(
            rotated=False,
            archived_entries=total_archived,
            archive_files=archive_paths,
            retained_entries=len(plan.retained),
            commit_sha=None,
            error=None,
        )

    # Pre-flight: validate every archive for duplicates and snapshot pre-rotation
    # state before any writes. A collision on the Nth month or a read failure
    # here must surface as LogRotationError, never a raw OSError that would
    # bypass run_pending's swallow-and-continue handler.
    try:
        for archive_path, (_ym, ym_entries) in zip(
            archive_paths, plan.to_archive.items(), strict=True
        ):
            _check_archive_for_duplicates(archive_path, ym_entries)
        original_log_text = log_path.read_text(encoding="utf-8")
        archive_originals: list[tuple[Path, str | None]] = [
            (p, p.read_text(encoding="utf-8") if p.exists() else None) for p in archive_paths
        ]
        archive_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LogRotationError(f"rotation pre-flight failed: {exc}") from exc

    touched: list[Path] = []
    assert plan.oldest_archived_month is not None
    try:
        for archive_path, (_ym, ym_entries) in zip(
            archive_paths, plan.to_archive.items(), strict=True
        ):
            _append_to_archive(archive_path, ym_entries, today_iso)
            touched.append(archive_path)
        _rewrite_log_md(log_path, frontmatter, plan.retained)
        touched.append(log_path)
        sha = _commit_rotation(wiki_dir, touched, plan.oldest_archived_month)
    except (WikiCommitError, OSError) as exc:
        try:
            log_path.write_text(original_log_text, encoding="utf-8")
            for archive_path, original in archive_originals:
                if original is None:
                    archive_path.unlink(missing_ok=True)
                else:
                    archive_path.write_text(original, encoding="utf-8")
        except OSError as rb_exc:
            logger.warn("log rotation rollback failed: %s", rb_exc)
        if isinstance(exc, WikiCommitError):
            raise LogRotationError(f"git commit failed: {exc}") from exc
        raise LogRotationError(f"rotation write failed: {exc}") from exc

    return RotateOutcome(
        rotated=True,
        archived_entries=total_archived,
        archive_files=archive_paths,
        retained_entries=len(plan.retained),
        commit_sha=sha,
        error=None,
    )


__all__ = [
    "ARCHIVE_DIR",
    "RETAIN_MIN",
    "ROTATE_THRESHOLD",
    "LogEntry",
    "LogRotationDuplicateError",
    "LogRotationError",
    "RotateOutcome",
    "RotationPlan",
    "rotate_log",
]
