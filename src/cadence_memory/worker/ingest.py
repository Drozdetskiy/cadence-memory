"""Ingest orchestrator: turn an IngestEvent into a wiki commit via Claude (design2 §7)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from string import Template

from cadence_memory.config.schema import Config, RepoConfig
from cadence_memory.documents.frontmatter import FrontmatterError, parse_page
from cadence_memory.executor.runner import ClaudeRunner
from cadence_memory.executor.tool_sets import WIKI_READWRITE
from cadence_memory.git.cache import GitCache
from cadence_memory.git.walker import (
    IngestEvent,
    NoiseBatchEvent,
    SingleCommitEvent,
    event_head_sha,
)
from cadence_memory.progress.events import (
    ErrorEvent,
    IngestEndEvent,
    IngestStartEvent,
)
from cadence_memory.progress.logger import Logger, NullLogger
from cadence_memory.worker.diff_budget import maybe_shard_diff, shard_noise_batch
from cadence_memory.worker.wiki_commit import (
    append_log_failure,
    list_touched_paths,
    revert_wiki,
    stage_and_commit,
)

_NULL_LOGGER: Logger = NullLogger()

_INDEX_HEAD_LINES = 60
_LOG_TAIL_ENTRIES = 15
_SUBJECT_MAX = 72


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Result of a single `ingest_event` call (design2 §7).

    On failure the caller is expected to leave ``state.last_sha`` unchanged
    even though ``head_sha`` is populated — advance only when ``success`` is
    true.
    """

    success: bool
    head_sha: str
    pages_touched: tuple[Path, ...]
    cost_usd: float | None
    duration_ms: int | None
    error: str | None
    wiki_commit_sha: str | None


def _load_default_template() -> str:
    resource = files("cadence_memory.defaults").joinpath("prompts/ingest.txt")
    return resource.read_text(encoding="utf-8")


def _index_head(wiki_dir: Path, *, max_lines: int = _INDEX_HEAD_LINES) -> str:
    path = wiki_dir / "index.md"
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[:max_lines])


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


def _subject_and_body_for_event(event: IngestEvent) -> tuple[str, str]:
    match event:
        case SingleCommitEvent(commit=c):
            return c.subject, c.body
        case NoiseBatchEvent() as nb:
            subject = f"{len(nb.commits)} noise commits"
            body_lines = [f"- {c.short_sha} {c.subject}" for c in nb.commits]
            return subject, "\n".join(body_lines)


def _changed_files_for_event(event: IngestEvent, cache: GitCache, repo_name: str) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    shas: tuple[str, ...]
    match event:
        case SingleCommitEvent(commit=c):
            shas = (c.sha,)
        case NoiseBatchEvent() as nb:
            shas = tuple(c.sha for c in nb.commits)
    for sha in shas:
        for path in cache.changed_files(name=repo_name, sha=sha):
            if path not in seen:
                seen.add(path)
                ordered.append(path)
    return "\n".join(ordered)


def _shard_event_diff(event: IngestEvent, cache: GitCache, repo_name: str) -> tuple[str, ...]:
    match event:
        case SingleCommitEvent(commit=c):
            return maybe_shard_diff(cache.diff(name=repo_name, sha=c.sha))
        case NoiseBatchEvent() as nb:
            per_commit = tuple(
                f"# Commit {commit.short_sha} — {commit.subject}\n"
                + cache.diff(name=repo_name, sha=commit.sha)
                for commit in nb.commits
            )
            return shard_noise_batch(per_commit)


def _render_prompt(template_text: str, **substitutions: str) -> str:
    return Template(template_text).substitute(**substitutions)


def _commit_message(repo_name: str, short_sha: str, subject: str) -> str:
    return f"cadence-memory: ingest {repo_name} {short_sha} {subject[:_SUBJECT_MAX]}"


def _short_sha_for_event(event: IngestEvent) -> str:
    match event:
        case SingleCommitEvent(commit=c):
            return c.short_sha
        case NoiseBatchEvent() as nb:
            return nb.head_sha[:7]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def ingest_event(
    *,
    event: IngestEvent,
    repo_cfg: RepoConfig,
    config: Config,
    wiki_dir: Path,
    runner: ClaudeRunner,
    cache: GitCache,
    prompt_template: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
    logger: Logger = _NULL_LOGGER,
) -> IngestOutcome:
    """Run a single ingest for `event` and return an `IngestOutcome`.

    On failure the caller is expected to NOT advance ``state.last_sha``; the
    returned ``head_sha`` is still populated so the caller can log the
    failure verbatim. The function never mutates ``state.json`` itself.
    """
    template_text = prompt_template if prompt_template is not None else _load_default_template()
    model = repo_cfg.model or config.model

    head_sha = event_head_sha(event)
    short_sha = _short_sha_for_event(event)
    subject, body = _subject_and_body_for_event(event)
    changed_files = _changed_files_for_event(event, cache, repo_cfg.name)
    shards = _shard_event_diff(event, cache, repo_cfg.name)
    index_head = _index_head(wiki_dir)
    log_tail = _log_tail(wiki_dir)
    wiki_root = str(wiki_dir)

    total = len(shards)

    costs: list[float] = []
    durations: list[int] = []
    touched: tuple[Path, ...] = ()
    pre_dirty = frozenset(list_touched_paths(wiki_dir))
    ingest_start = datetime.now(UTC)

    def _aggregated_cost() -> float | None:
        return sum(costs) if costs else None

    def _aggregated_duration() -> int | None:
        return sum(durations) if durations else None

    def _wall_duration_ms() -> int:
        return int((datetime.now(UTC) - ingest_start).total_seconds() * 1000)

    logger.log_event(
        IngestStartEvent(
            repo=repo_cfg.name,
            commit_sha=head_sha,
            subject=subject,
        )
    )

    for idx, shard in enumerate(shards):
        is_final = idx == total - 1
        if not is_final:
            effective_subject = f"{subject} [shard {idx + 1} of {total} — do NOT append to log.md]"
        else:
            effective_subject = subject

        logger.section(f"ingest {repo_cfg.name}@{short_sha} ({idx + 1}/{total})")

        rendered = _render_prompt(
            template_text,
            repo_name=repo_cfg.name,
            commit_subject=effective_subject,
            commit_body=body,
            short_sha=short_sha,
            changed_files=changed_files,
            diff=shard,
            index_head=index_head,
            log_tail=log_tail,
            wiki_root=wiki_root,
        )

        result = runner.run(
            prompt=rendered,
            model=model,
            allowed_tools=WIKI_READWRITE,
            idle_timeout_s=config.idle_timeout_s,
            cwd=wiki_dir,
            logger=logger,
            phase=f"ingest-{short_sha}",
        )
        if result.cost_usd is not None:
            costs.append(result.cost_usd)
        if result.duration_ms is not None:
            durations.append(result.duration_ms)

        if not result.success:
            logger.log_event(
                ErrorEvent(
                    phase="ingest",
                    message=result.error or "claude run failed",
                )
            )
            revert_wiki(wiki_dir, preserve=pre_dirty)
            today_iso = clock().date().isoformat()
            append_log_failure(
                wiki_dir=wiki_dir,
                repo_name=repo_cfg.name,
                short_sha=short_sha,
                subject=subject,
                error=result.error or "claude run failed",
                today_iso=today_iso,
            )
            logger.log_event(
                IngestEndEvent(
                    repo=repo_cfg.name,
                    commit_sha=head_sha,
                    duration_ms=_wall_duration_ms(),
                    result="failed",
                    pages_touched=0,
                    cost_usd_estimate=_aggregated_cost(),
                )
            )
            return IngestOutcome(
                success=False,
                head_sha=head_sha,
                pages_touched=(),
                cost_usd=_aggregated_cost(),
                duration_ms=_aggregated_duration(),
                error=result.error,
                wiki_commit_sha=None,
            )

        touched = list_touched_paths(wiki_dir)
        for path in touched:
            if path.suffix != ".md" or not path.is_file():
                continue
            try:
                parse_page(path)
            except FrontmatterError as exc:
                logger.log_event(
                    ErrorEvent(
                        phase="ingest",
                        message="frontmatter error",
                        detail=str(exc),
                    )
                )
                revert_wiki(wiki_dir, preserve=pre_dirty)
                today_iso = clock().date().isoformat()
                append_log_failure(
                    wiki_dir=wiki_dir,
                    repo_name=repo_cfg.name,
                    short_sha=short_sha,
                    subject=subject,
                    error=str(exc),
                    today_iso=today_iso,
                )
                logger.log_event(
                    IngestEndEvent(
                        repo=repo_cfg.name,
                        commit_sha=head_sha,
                        duration_ms=_wall_duration_ms(),
                        result="failed",
                        pages_touched=0,
                        cost_usd_estimate=_aggregated_cost(),
                    )
                )
                return IngestOutcome(
                    success=False,
                    head_sha=head_sha,
                    pages_touched=(),
                    cost_usd=_aggregated_cost(),
                    duration_ms=_aggregated_duration(),
                    error=str(exc),
                    wiki_commit_sha=None,
                )

    wiki_sha = stage_and_commit(
        wiki_dir=wiki_dir,
        message=_commit_message(repo_cfg.name, short_sha, subject),
    )

    logger.log_event(
        IngestEndEvent(
            repo=repo_cfg.name,
            commit_sha=head_sha,
            duration_ms=_wall_duration_ms(),
            result="ok",
            pages_touched=len(touched),
            cost_usd_estimate=_aggregated_cost(),
        )
    )

    return IngestOutcome(
        success=True,
        head_sha=head_sha,
        pages_touched=touched,
        cost_usd=_aggregated_cost(),
        duration_ms=_aggregated_duration(),
        error=None,
        wiki_commit_sha=wiki_sha,
    )


__all__ = ["IngestOutcome", "ingest_event"]
