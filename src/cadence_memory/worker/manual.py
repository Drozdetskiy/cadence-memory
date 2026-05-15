"""Manual ingest orchestrator: integrate a single non-commit source via Claude (design2 §11)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from string import Template

from cadence_memory.config.schema import Config
from cadence_memory.documents.frontmatter import FrontmatterError, parse_page
from cadence_memory.executor.runner import ClaudeRunner
from cadence_memory.executor.tool_sets import WIKI_READWRITE
from cadence_memory.progress.events import ErrorEvent, PhaseEndEvent, PhaseStartEvent
from cadence_memory.progress.logger import Logger, NullLogger
from cadence_memory.worker.wiki_commit import (
    append_log_failure,
    list_touched_paths,
    revert_wiki,
    stage_and_commit,
)

_NULL_LOGGER: Logger = NullLogger()

# duplication intentional — see TASK 1016
_INDEX_HEAD_LINES = 60
_LOG_TAIL_ENTRIES = 15

_TRUNCATION_LIMIT = 250 * 1024
_TRUNCATION_MARKER = "\n\n[truncated by cadence-memory at 250 KB]\n"


@dataclass(frozen=True, slots=True)
class ManualIngestOutcome:
    """Result of a single `ingest_file` call (design2 §11)."""

    success: bool
    source_path: Path
    pages_touched: tuple[Path, ...]
    wiki_commit_sha: str | None
    cost_usd: float | None
    error: str | None


def sniff_source_kind(path: Path) -> str:
    """Filename-based heuristic to classify a manual ingest source."""
    name = path.name.lower()
    if "meeting" in name or "standup" in name:
        return "meeting"
    if "spec" in name or "rfc" in name:
        return "spec"
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return "article"
    return "other"


def _read_source(path: Path) -> str:
    suffix = path.suffix.lower()
    text: str | None = None
    if suffix == ".md":
        try:
            text = parse_page(path).body
        except FrontmatterError:
            text = None
    if text is None:
        text = path.read_bytes().decode("utf-8", errors="replace")
    if len(text) > _TRUNCATION_LIMIT:
        text = text[:_TRUNCATION_LIMIT] + _TRUNCATION_MARKER
    return text


# duplication intentional — see TASK 1016
def _index_head(wiki_dir: Path, *, max_lines: int = _INDEX_HEAD_LINES) -> str:
    path = wiki_dir / "index.md"
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[:max_lines])


# duplication intentional — see TASK 1016
def _log_tail(wiki_dir: Path, *, max_entries: int = _LOG_TAIL_ENTRIES) -> str:
    path = wiki_dir / "log.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    entry_starts: list[int] = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if not entry_starts:
        return ""
    start_idx = entry_starts[-max_entries] if len(entry_starts) > max_entries else entry_starts[0]
    return "\n".join(lines[start_idx:])


def _load_default_template() -> str:
    resource = files("cadence_memory.defaults").joinpath("prompts/manual-ingest.txt")
    return resource.read_text(encoding="utf-8")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _failure_short_sha(source_path: Path) -> str:
    candidate = source_path.name[:7]
    return candidate if candidate else "manual"


def ingest_file(
    *,
    source_path: Path,
    config: Config,
    wiki_dir: Path,
    runner: ClaudeRunner,
    prompt_template: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
    logger: Logger = _NULL_LOGGER,
) -> ManualIngestOutcome:
    """Run a single manual ingest for `source_path` and return a `ManualIngestOutcome`.

    On failure the wiki working tree is reverted and a FAILED entry is
    appended to ``log.md``; on success the staged changes are committed
    with the message ``cadence-memory: ingest-manual <basename>``.
    """
    template_text = prompt_template if prompt_template is not None else _load_default_template()

    source_kind = sniff_source_kind(source_path)
    source_content = _read_source(source_path)
    index_head = _index_head(wiki_dir)
    log_tail = _log_tail(wiki_dir)
    wiki_root = str(wiki_dir)

    pre_dirty = frozenset(list_touched_paths(wiki_dir))

    rendered = Template(template_text).substitute(
        wiki_root=wiki_root,
        source_path=str(source_path),
        source_kind=source_kind,
        source_content=source_content,
        index_head=index_head,
        log_tail=log_tail,
    )

    phase_start = datetime.now(UTC)
    logger.log_event(PhaseStartEvent("manual-ingest", source=str(source_path)))

    result = runner.run(
        prompt=rendered,
        model=config.model,
        allowed_tools=WIKI_READWRITE,
        idle_timeout_s=config.idle_timeout_s,
        cwd=wiki_dir,
        logger=logger,
        phase="manual-ingest",
    )

    if not result.success:
        logger.log_event(
            ErrorEvent(
                phase="manual-ingest",
                message=result.error or "claude run failed",
            )
        )
        revert_wiki(wiki_dir, preserve=pre_dirty)
        today_iso = clock().date().isoformat()
        append_log_failure(
            wiki_dir=wiki_dir,
            repo_name="manual",
            short_sha=_failure_short_sha(source_path),
            subject=source_path.name,
            error=result.error or "claude run failed",
            today_iso=today_iso,
        )
        phase_duration_ms = int((datetime.now(UTC) - phase_start).total_seconds() * 1000)
        logger.log_event(
            PhaseEndEvent(
                "manual-ingest",
                duration_ms=phase_duration_ms,
                result="failed",
                cost_usd_estimate=result.cost_usd,
            )
        )
        return ManualIngestOutcome(
            success=False,
            source_path=source_path,
            pages_touched=(),
            wiki_commit_sha=None,
            cost_usd=result.cost_usd,
            error=result.error,
        )

    touched = list_touched_paths(wiki_dir)
    for path in touched:
        if path in pre_dirty:
            continue
        if path.suffix != ".md" or not path.is_file():
            continue
        try:
            parse_page(path)
        except FrontmatterError as exc:
            logger.log_event(
                ErrorEvent(
                    phase="manual-ingest",
                    message="frontmatter error",
                    detail=str(exc),
                )
            )
            revert_wiki(wiki_dir, preserve=pre_dirty)
            today_iso = clock().date().isoformat()
            append_log_failure(
                wiki_dir=wiki_dir,
                repo_name="manual",
                short_sha=_failure_short_sha(source_path),
                subject=source_path.name,
                error=str(exc),
                today_iso=today_iso,
            )
            phase_duration_ms = int((datetime.now(UTC) - phase_start).total_seconds() * 1000)
            logger.log_event(
                PhaseEndEvent(
                    "manual-ingest",
                    duration_ms=phase_duration_ms,
                    result="failed",
                    cost_usd_estimate=result.cost_usd,
                )
            )
            return ManualIngestOutcome(
                success=False,
                source_path=source_path,
                pages_touched=(),
                wiki_commit_sha=None,
                cost_usd=result.cost_usd,
                error=str(exc),
            )

    wiki_sha = stage_and_commit(
        wiki_dir=wiki_dir,
        message=f"cadence-memory: ingest-manual {source_path.name}",
    )

    phase_duration_ms = int((datetime.now(UTC) - phase_start).total_seconds() * 1000)
    logger.log_event(
        PhaseEndEvent(
            "manual-ingest",
            duration_ms=phase_duration_ms,
            result="ok",
            cost_usd_estimate=result.cost_usd,
        )
    )

    return ManualIngestOutcome(
        success=True,
        source_path=source_path,
        pages_touched=touched,
        wiki_commit_sha=wiki_sha,
        cost_usd=result.cost_usd,
        error=None,
    )


__all__ = ["ManualIngestOutcome", "ingest_file", "sniff_source_kind"]
