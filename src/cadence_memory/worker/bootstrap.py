"""Bootstrap orchestrator: run the 5-stage initial pass for a single repo (design2 §8)."""

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
from cadence_memory.worker.wiki_commit import (
    append_log_failure,
    list_touched_paths,
    revert_wiki,
    stage_and_commit,
)

_STAGE_FILES: dict[int, str] = {
    1: "bootstrap-1-data-model.txt",
    2: "bootstrap-2-routes.txt",
    3: "bootstrap-3-architecture.txt",
    4: "bootstrap-4-gaps.txt",
    5: "bootstrap-5-plans.txt",
}

_PLANS_DIRS: tuple[str, ...] = ("plans", "todos", "docs/decisions", "ADR")

_PLANS_DIRECTIVE_INGEST = (
    "Plan / todo / decision sources were detected in the repository. "
    "Proceed with the ingest steps below and append "
    "`## [$today] bootstrap-5 | $repo_name $head_sha — plans` to $wiki_root/log.md."
)

_PLANS_DIRECTIVE_SKIP = (
    "No plans/, todos/, docs/decisions/, or ADR/ directory was found in the repository. "
    "Do NOT generate any plan pages. Append only the following entry to $wiki_root/log.md "
    "and return: "
    "`## [$today] bootstrap-5 | $repo_name $head_sha — skipped (no plans/todos found)`."
)


@dataclass(frozen=True, slots=True)
class BootstrapOutcome:
    """Result of a single `run_bootstrap` call (design2 §8).

    ``stages_run`` records every stage that was attempted (which equals the
    ``stages`` argument). ``stages_failed`` is the subset that did not produce
    a clean wiki commit. The CLI decides whether to advance ``last_sha`` —
    this orchestrator never mutates ``state.json``.
    """

    stages_run: tuple[int, ...]
    stages_failed: tuple[int, ...]
    head_sha: str
    cost_usd_total: float
    pages_touched_total: int


def _load_stage_template(stage: int) -> str:
    filename = _STAGE_FILES[stage]
    resource = files("cadence_memory.defaults").joinpath(f"prompts/{filename}")
    return resource.read_text(encoding="utf-8")


def _detect_plans_dirs(repo_path: Path) -> bool:
    return any((repo_path / relpath).is_dir() for relpath in _PLANS_DIRS)


def _render_prompt(template_text: str, **substitutions: str) -> str:
    return Template(template_text).substitute(**substitutions)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_bootstrap(
    *,
    repo_cfg: RepoConfig,
    config: Config,
    wiki_dir: Path,
    cache: GitCache,
    runner: ClaudeRunner,
    stages: tuple[int, ...] = (1, 2, 3, 4, 5),
    clock: Callable[[], datetime] = _utc_now,
) -> BootstrapOutcome:
    """Run the five-stage bootstrap for `repo_cfg` and return a `BootstrapOutcome`.

    Stages run sequentially. A failing stage does not abort the rest — each
    stage produces its own wiki commit (or revert+failure log entry). The
    caller is responsible for advancing ``state.last_sha`` based on the
    returned ``stages_failed`` set.
    """
    clone = cache.ensure(name=repo_cfg.name, url=repo_cfg.url, branch=repo_cfg.branch)
    repo_path = clone.path
    head_sha = clone.head_sha
    short_sha = head_sha[:7]

    model = repo_cfg.model or config.model
    budget_usd = repo_cfg.budget_usd if repo_cfg.budget_usd is not None else config.budget_usd

    failed: list[int] = []
    cost_total = 0.0
    pages_total = 0

    plans_present = _detect_plans_dirs(repo_path)
    plans_directive_template = _PLANS_DIRECTIVE_INGEST if plans_present else _PLANS_DIRECTIVE_SKIP

    for stage in stages:
        pre_dirty = frozenset(list_touched_paths(wiki_dir))
        template_text = _load_stage_template(stage)
        today_iso = clock().date().isoformat()
        subs: dict[str, str] = {
            "repo_name": repo_cfg.name,
            "repo_path": str(repo_path),
            "wiki_root": str(wiki_dir),
            "head_sha": head_sha,
            "today": today_iso,
        }
        if stage == 5:
            subs["plans_directive"] = Template(plans_directive_template).substitute(**subs)
        rendered = _render_prompt(template_text, **subs)

        result = runner.run(
            prompt=rendered,
            model=model,
            budget_usd=budget_usd,
            allowed_tools=WIKI_READWRITE,
            idle_timeout_s=config.idle_timeout_s,
            cwd=wiki_dir,
        )
        if result.cost_usd is not None:
            cost_total += result.cost_usd

        subject = f"bootstrap-{stage}"

        if not result.success:
            revert_wiki(wiki_dir, preserve=pre_dirty)
            append_log_failure(
                wiki_dir=wiki_dir,
                repo_name=repo_cfg.name,
                short_sha=short_sha,
                subject=subject,
                error=result.error or "claude run failed",
                today_iso=today_iso,
            )
            failed.append(stage)
            continue

        touched = list_touched_paths(wiki_dir)
        frontmatter_error: str | None = None
        for path in touched:
            if path.suffix != ".md" or not path.is_file():
                continue
            try:
                parse_page(path)
            except FrontmatterError as exc:
                frontmatter_error = str(exc)
                break

        if frontmatter_error is not None:
            revert_wiki(wiki_dir, preserve=pre_dirty)
            append_log_failure(
                wiki_dir=wiki_dir,
                repo_name=repo_cfg.name,
                short_sha=short_sha,
                subject=subject,
                error=frontmatter_error,
                today_iso=today_iso,
            )
            failed.append(stage)
            continue

        stage_and_commit(
            wiki_dir=wiki_dir,
            message=f"cadence-memory: {subject} {repo_cfg.name} {short_sha}",
        )
        pages_total += len(touched)

    return BootstrapOutcome(
        stages_run=tuple(stages),
        stages_failed=tuple(failed),
        head_sha=head_sha,
        cost_usd_total=cost_total,
        pages_touched_total=pages_total,
    )


__all__ = ["BootstrapOutcome", "run_bootstrap"]
