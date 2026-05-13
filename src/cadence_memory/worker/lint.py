"""Wiki lint orchestrator: audit the wiki itself via Claude (design2 §9, §11)."""

from __future__ import annotations

import subprocess
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
from cadence_memory.wiki.branch import create_or_switch_branch
from cadence_memory.worker.wiki_commit import (
    append_log_failure,
    list_touched_paths,
    revert_wiki,
    stage_and_commit,
)

# duplication intentional — see TASK 1016
_INDEX_HEAD_LINES = 60
_LOG_TAIL_ENTRIES = 15


@dataclass(frozen=True, slots=True)
class LintOutcome:
    """Result of a single `run_lint` call (design2 §9)."""

    success: bool
    pages_touched: tuple[Path, ...]
    branch_used: str
    previous_branch: str | None
    wiki_commit_sha: str | None
    cost_usd: float | None
    error: str | None


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
    resource = files("cadence_memory.defaults").joinpath("prompts/lint.txt")
    return resource.read_text(encoding="utf-8")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _current_branch(wiki_dir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=wiki_dir,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _scope_hint(only_repo: str | None) -> str:
    if not only_repo:
        return ""
    return f"\nScope your audit to projects/{only_repo}/.\n"


def run_lint(
    *,
    wiki_dir: Path,
    config: Config,
    runner: ClaudeRunner,
    apply: bool = False,
    only_repo: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
    prompt_template: str | None = None,
) -> LintOutcome:
    """Audit the wiki via Claude and return a `LintOutcome`.

    With ``apply=False`` the working tree is switched to ``lint/<today>``
    (created on demand) before invoking Claude; with ``apply=True`` the
    current branch is used. Failure paths revert the wiki and append a
    FAILED log entry; success stages and commits with subject
    ``cadence-memory: lint <today>``.
    """
    template_text = prompt_template if prompt_template is not None else _load_default_template()

    today_iso = clock().date().isoformat()
    branch_name = f"lint/{today_iso}"

    previous_branch: str | None
    if apply:
        branch_used = _current_branch(wiki_dir)
        previous_branch = None
    else:
        previous_branch = create_or_switch_branch(wiki_dir, branch_name)
        branch_used = branch_name

    pre_dirty = frozenset(list_touched_paths(wiki_dir))

    rendered = Template(template_text).substitute(
        wiki_root=str(wiki_dir),
        today=today_iso,
        pages_index_excerpt=_index_head(wiki_dir),
        recent_log_tail=_log_tail(wiki_dir),
        scope_hint=_scope_hint(only_repo),
    )

    result = runner.run(
        prompt=rendered,
        model=config.model,
        budget_usd=config.budget_usd,
        allowed_tools=WIKI_READWRITE,
        idle_timeout_s=config.idle_timeout_s,
        cwd=wiki_dir,
    )

    if not result.success:
        revert_wiki(wiki_dir, preserve=pre_dirty)
        append_log_failure(
            wiki_dir=wiki_dir,
            repo_name="lint",
            short_sha="lint",
            subject=branch_name,
            error=result.error or "claude run failed",
            today_iso=today_iso,
        )
        return LintOutcome(
            success=False,
            pages_touched=(),
            branch_used=branch_used,
            previous_branch=previous_branch,
            wiki_commit_sha=None,
            cost_usd=result.cost_usd,
            error=result.error,
        )

    touched = list_touched_paths(wiki_dir)
    for path in touched:
        if path.suffix != ".md" or not path.is_file():
            continue
        try:
            parse_page(path)
        except FrontmatterError as exc:
            revert_wiki(wiki_dir, preserve=pre_dirty)
            append_log_failure(
                wiki_dir=wiki_dir,
                repo_name="lint",
                short_sha="lint",
                subject=branch_name,
                error=str(exc),
                today_iso=today_iso,
            )
            return LintOutcome(
                success=False,
                pages_touched=(),
                branch_used=branch_used,
                previous_branch=previous_branch,
                wiki_commit_sha=None,
                cost_usd=result.cost_usd,
                error=str(exc),
            )

    wiki_sha = stage_and_commit(
        wiki_dir=wiki_dir,
        message=f"cadence-memory: lint {today_iso}",
    )

    return LintOutcome(
        success=True,
        pages_touched=touched,
        branch_used=branch_used,
        previous_branch=previous_branch,
        wiki_commit_sha=wiki_sha,
        cost_usd=result.cost_usd,
        error=None,
    )


__all__ = ["LintOutcome", "run_lint"]
