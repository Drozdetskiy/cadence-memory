"""Tests for the wiki lint orchestrator (design2 §9, §11)."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from string import Template

import pytest

from cadence_memory.config.schema import Config, WorkerConfig
from cadence_memory.executor.runner import ClaudeResult
from cadence_memory.executor.tool_sets import WIKI_READWRITE
from cadence_memory.worker.lint import LintOutcome, run_lint

_VALID_PAGE = (
    "---\n"
    'title: "Sample"\n'
    "type: overview\n"
    "project: project-a\n"
    "created: 2026-05-12\n"
    "updated: 2026-05-12\n"
    "tags: []\n"
    "confidence: high\n"
    "---\n"
    "\n"
    "**TLDR**: hello.\n"
)


@dataclass
class _RunCall:
    prompt: str
    model: str
    budget_usd: float | None
    allowed_tools: tuple[str, ...]
    idle_timeout_s: int
    cwd: Path | None


@dataclass
class _FakeClaudeRunner:
    side_effects: list[Callable[[Path], ClaudeResult]] = field(default_factory=list)
    default_result: ClaudeResult = field(
        default_factory=lambda: ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.01,
            duration_ms=123,
            tool_call_count=1,
            error=None,
        )
    )
    calls: list[_RunCall] = field(default_factory=list)

    def run(
        self,
        *,
        prompt: str,
        model: str,
        budget_usd: float | None,
        allowed_tools: tuple[str, ...],
        idle_timeout_s: int,
        cwd: Path | None = None,
    ) -> ClaudeResult:
        self.calls.append(
            _RunCall(
                prompt=prompt,
                model=model,
                budget_usd=budget_usd,
                allowed_tools=allowed_tools,
                idle_timeout_s=idle_timeout_s,
                cwd=cwd,
            )
        )
        if self.side_effects:
            side_effect = self.side_effects.pop(0)
            assert cwd is not None
            return side_effect(cwd)
        return self.default_result


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def _init_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git("init", "--initial-branch=main", cwd=wiki)
    _git("config", "user.email", "test@example.com", cwd=wiki)
    _git("config", "user.name", "Test User", cwd=wiki)
    (wiki / "index.md").write_text(
        "---\n"
        'title: "Index"\n'
        "type: overview\n"
        "project: _master\n"
        "created: 2026-05-12\n"
        "updated: 2026-05-12\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
        "\n"
        "**TLDR**: catalog.\n"
        "## Cross-project root pages\n"
        "- [[index]]\n"
        "- [[log]]\n",
        encoding="utf-8",
    )
    (wiki / "log.md").write_text(
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
        "## [2026-05-12] init | wiki scaffolded\n"
        "\n"
        "seed entry.\n",
        encoding="utf-8",
    )
    _git("add", "-A", cwd=wiki)
    _git("commit", "-m", "seed", cwd=wiki)
    return wiki


def _config(**overrides: object) -> Config:
    base: dict[str, object] = {
        "model": "claude-sonnet-4-6",
        "budget_usd": 0.5,
        "idle_timeout_s": 300,
        "worker": WorkerConfig(),
        "repos": (),
    }
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def _fixed_clock(year: int = 2026, month: int = 5, day: int = 12) -> Callable[[], datetime]:
    return lambda: datetime(year, month, day, 12, 0, 0, tzinfo=UTC)


def _load_lint_template() -> str:
    resource = files("cadence_memory.defaults").joinpath("prompts/lint.txt")
    return resource.read_text(encoding="utf-8")


def _current_branch(wiki: Path) -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wiki).stdout.strip()


def test_prompt_template_contains_all_placeholders() -> None:
    text = _load_lint_template()
    for token in (
        "$wiki_root",
        "$today",
        "$pages_index_excerpt",
        "$recent_log_tail",
        "$scope_hint",
    ):
        assert token in text


def test_creates_lint_branch_by_default(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.branch_used == "lint/2026-05-12"
    assert outcome.previous_branch == "main"
    assert _current_branch(wiki) == "lint/2026-05-12"


def test_apply_stays_on_current_branch(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _git("checkout", "-b", "feature/x", cwd=wiki)
    runner = _FakeClaudeRunner()

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        apply=True,
        clock=_fixed_clock(),
    )

    assert outcome.branch_used == "feature/x"
    assert outcome.previous_branch is None
    assert _current_branch(wiki) == "feature/x"


def test_idempotent_branch_creation(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _git("branch", "lint/2026-05-12", cwd=wiki)
    runner = _FakeClaudeRunner()

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success
    assert outcome.branch_used == "lint/2026-05-12"
    assert _current_branch(wiki) == "lint/2026-05-12"


def test_only_repo_injects_scope_into_prompt(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()

    run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        only_repo="project-a",
        clock=_fixed_clock(),
    )

    assert "projects/project-a/" in runner.calls[0].prompt


def test_no_scope_hint_when_only_repo_absent(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()

    run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert "projects/" not in runner.calls[0].prompt


def test_prompt_includes_index_head_and_log_tail(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()

    run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    prompt = runner.calls[0].prompt
    assert "Cross-project root pages" in prompt
    assert "[2026-05-12] init | wiki scaffolded" in prompt


def test_prompt_has_no_unsubstituted_placeholders(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()

    run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        only_repo="project-a",
        clock=_fixed_clock(),
    )

    template_text = _load_lint_template()
    identifiers = set(Template(template_text).get_identifiers())
    rendered = runner.calls[0].prompt
    for ident in identifiers:
        assert f"${ident}" not in rendered, f"placeholder {ident!r} not substituted"


def test_pages_touched_validated(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_bad(cwd: Path) -> ClaudeResult:
        (cwd / "stub.md").write_text("no frontmatter here\n", encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.02,
            duration_ms=120,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_bad])

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.wiki_commit_sha is None
    assert outcome.error is not None
    assert not (wiki / "stub.md").exists()
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | lint" in log_text
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=wiki,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert porcelain == ""


def test_runner_failure_reverts_and_logs(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def fail(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.01,
            duration_ms=50,
            tool_call_count=0,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[fail])

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.error == "boom"
    assert outcome.wiki_commit_sha is None
    assert outcome.branch_used == "lint/2026-05-12"
    assert outcome.previous_branch == "main"
    assert _current_branch(wiki) == "lint/2026-05-12"
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | lint" in log_text
    assert "boom" in log_text


def test_apply_runner_failure_logs_on_current_branch(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _git("checkout", "-b", "feature/x", cwd=wiki)

    def fail(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.01,
            duration_ms=50,
            tool_call_count=0,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[fail])

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        apply=True,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.error == "boom"
    assert outcome.branch_used == "feature/x"
    assert outcome.previous_branch is None
    assert _current_branch(wiki) == "feature/x"
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | lint" in log_text
    branches = _git("branch", "--list", "lint/2026-05-12", cwd=wiki).stdout.strip()
    assert branches == ""


def test_happy_path_commits_wiki(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_page(cwd: Path) -> ClaudeResult:
        (cwd / "learnings.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.03,
            duration_ms=400,
            tool_call_count=2,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_page])

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.wiki_commit_sha is not None
    subject_line = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert subject_line == "cadence-memory: lint 2026-05-12"
    assert outcome.cost_usd == 0.03
    assert outcome.branch_used == "lint/2026-05-12"


def test_runner_called_with_wiki_readwrite_and_config_defaults(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()

    run_lint(
        wiki_dir=wiki,
        config=_config(model="claude-opus-4-7", budget_usd=1.5, idle_timeout_s=600),
        runner=runner,
        clock=_fixed_clock(),
    )

    call = runner.calls[0]
    assert call.allowed_tools == WIKI_READWRITE
    assert call.model == "claude-opus-4-7"
    assert call.budget_usd == 1.5
    assert call.idle_timeout_s == 600
    assert call.cwd == wiki


def test_run_lint_preserves_user_dirty_file_on_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "config.yaml").write_text("model: old\n", encoding="utf-8")
    _git("add", "config.yaml", cwd=wiki)
    _git("commit", "-m", "add config", cwd=wiki)
    (wiki / "config.yaml").write_text("model: user-edit\n", encoding="utf-8")

    def fail(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=None,
            duration_ms=None,
            tool_call_count=0,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[fail])

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert (wiki / "config.yaml").read_text(encoding="utf-8") == "model: user-edit\n"


def test_run_lint_preserves_user_dirty_file_on_frontmatter_error(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "config.yaml").write_text("model: old\n", encoding="utf-8")
    _git("add", "config.yaml", cwd=wiki)
    _git("commit", "-m", "add config", cwd=wiki)
    (wiki / "config.yaml").write_text("model: user-edit\n", encoding="utf-8")

    def write_bad_frontmatter(cwd: Path) -> ClaudeResult:
        (cwd / "stub.md").write_text("no frontmatter here\n", encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.02,
            duration_ms=120,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_bad_frontmatter])

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert not (wiki / "stub.md").exists()
    assert (wiki / "config.yaml").read_text(encoding="utf-8") == "model: user-edit\n"


def test_run_lint_reverts_claude_changes_on_runner_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_then_fail(cwd: Path) -> ClaudeResult:
        (cwd / "claude_new.md").write_text("claude output\n", encoding="utf-8")
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=None,
            duration_ms=None,
            tool_call_count=1,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[write_then_fail])

    outcome = run_lint(
        wiki_dir=wiki,
        config=_config(),
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert not (wiki / "claude_new.md").exists()


def test_lint_outcome_is_frozen_slots() -> None:
    outcome = LintOutcome(
        success=True,
        pages_touched=(),
        branch_used="lint/2026-05-12",
        previous_branch="main",
        wiki_commit_sha=None,
        cost_usd=None,
        error=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.success = False  # type: ignore[misc]
    assert not hasattr(outcome, "__dict__")
