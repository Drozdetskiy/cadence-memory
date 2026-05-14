"""Tests for the bootstrap orchestrator (design2 §8)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cadence_memory.config.schema import Config, RepoConfig, WorkerConfig
from cadence_memory.executor.runner import ClaudeResult
from cadence_memory.executor.tool_sets import WIKI_READWRITE
from cadence_memory.git.cache import CloneResult, CommitInfo
from cadence_memory.progress.events import (
    PhaseEndEvent,
    PhaseStartEvent,
    ProgressEvent,
    StageEndEvent,
    StageStartEvent,
)
from cadence_memory.worker.bootstrap import (
    BootstrapOutcome,
    run_bootstrap,
)

_HEAD_SHA = "f" * 40
_SHORT_SHA = _HEAD_SHA[:7]


@dataclass
class _RunCall:
    prompt: str
    model: str
    allowed_tools: tuple[str, ...]
    idle_timeout_s: int
    cwd: Path | None


@dataclass
class _FakeClaudeRunner:
    """Mock ClaudeRunner recording call kwargs and letting each call mutate the wiki."""

    side_effects: list[Callable[[Path], ClaudeResult]] = field(default_factory=list)
    default_result: ClaudeResult = field(
        default_factory=lambda: ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.01,
            duration_ms=100,
            tool_call_count=0,
            error=None,
        )
    )
    calls: list[_RunCall] = field(default_factory=list)
    extra_kwargs: list[dict[str, object]] = field(default_factory=list)

    def run(
        self,
        *,
        prompt: str,
        model: str,
        allowed_tools: tuple[str, ...],
        idle_timeout_s: int,
        cwd: Path | None = None,
        **extra: object,
    ) -> ClaudeResult:
        self.calls.append(
            _RunCall(
                prompt=prompt,
                model=model,
                allowed_tools=allowed_tools,
                idle_timeout_s=idle_timeout_s,
                cwd=cwd,
            )
        )
        self.extra_kwargs.append(dict(extra))
        if self.side_effects:
            side_effect = self.side_effects.pop(0)
            assert cwd is not None
            return side_effect(cwd)
        return self.default_result


@dataclass
class _FakeGitCache:
    """Mock GitCache returning a pre-baked CloneResult."""

    clone_result: CloneResult

    def ensure(self, *, name: str, url: str, branch: str) -> CloneResult:
        return self.clone_result

    def head(self, *, name: str, branch: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:  # pragma: no cover - unused
        raise NotImplementedError

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:  # pragma: no cover - unused
        raise NotImplementedError

    def diff(self, *, name: str, sha: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:  # pragma: no cover - unused
        raise NotImplementedError

    def list_commits(  # pragma: no cover - unused
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]:
        raise NotImplementedError


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


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
        "**TLDR**: catalog.\n",
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


def _make_repo_path(tmp_path: Path, *, with_plans: bool = False) -> Path:
    src = tmp_path / "src-repo"
    src.mkdir()
    if with_plans:
        (src / "plans").mkdir()
        (src / "plans" / "rfc-001.md").write_text("draft\n", encoding="utf-8")
    return src


def _repo_cfg(**overrides: object) -> RepoConfig:
    base: dict[str, object] = {
        "name": "project-a",
        "url": "https://example.com/project-a.git",
        "branch": "main",
    }
    base.update(overrides)
    return RepoConfig(**base)  # type: ignore[arg-type]


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


def _success_result(cost_usd: float = 0.01) -> ClaudeResult:
    return ClaudeResult(
        success=True,
        final_text="ok",
        cost_usd=cost_usd,
        duration_ms=100,
        tool_call_count=1,
        error=None,
    )


def _make_runner(
    side_effects: list[Callable[[Path], ClaudeResult]] | None = None,
) -> _FakeClaudeRunner:
    runner = _FakeClaudeRunner()
    if side_effects is not None:
        runner.side_effects = side_effects
    return runner


def _clone(tmp_path: Path, *, with_plans: bool = False) -> CloneResult:
    return CloneResult(
        path=_make_repo_path(tmp_path, with_plans=with_plans),
        head_sha=_HEAD_SHA,
        was_initial_clone=False,
    )


def test_runs_all_five_stages(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 5
    assert outcome.stages_run == (1, 2, 3, 4, 5)
    prompts = [c.prompt for c in runner.calls]
    assert len(set(prompts)) == 5


def test_stack_detect_in_every_prompt(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    phrase = "detect the stack by reading manifest files"
    for call in runner.calls:
        assert phrase in call.prompt


def test_repo_path_substituted(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    clone = _clone(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=clone)

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    repo_path_str = str(clone.path)
    for call in runner.calls:
        assert repo_path_str in call.prompt
        assert _HEAD_SHA in call.prompt


def test_failing_stage_does_not_block_rest(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def fail_stage_two(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.02,
            duration_ms=50,
            tool_call_count=0,
            error="stage-2 boom",
        )

    runner = _make_runner(
        side_effects=[
            lambda cwd: _success_result(),
            fail_stage_two,
            lambda cwd: _success_result(),
            lambda cwd: _success_result(),
            lambda cwd: _success_result(),
        ]
    )
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.stages_run == (1, 2, 3, 4, 5)
    assert outcome.stages_failed == (2,)
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | project-a" in log_text
    assert "bootstrap-2" in log_text
    assert "stage-2 boom" in log_text


def test_state_advances_logic_via_outcome(tmp_path: Path) -> None:
    """run_bootstrap returns an outcome and does NOT touch any state.json itself.

    The CLI tests (task 5) cover the state-advance + --strict behaviour;
    here we just confirm the orchestrator stays state-free.
    """
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert isinstance(outcome, BootstrapOutcome)
    assert not (wiki / ".cadence-memory" / "state.json").exists()


def test_stage_5_skipped_when_no_plans_dir(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=_clone(tmp_path, with_plans=False))

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(5,),
        clock=_fixed_clock(),
    )

    stage5_prompt = runner.calls[0].prompt
    assert "skipped (no plans/todos found)" in stage5_prompt
    assert "No plans/, todos/, docs/decisions/, or ADR/ directory was found" in stage5_prompt
    assert "Plan / todo / decision sources were detected" not in stage5_prompt
    # The plans directive uses $today/$repo_name/$head_sha/$wiki_root inside
    # its body — those MUST be resolved (not survive as literal dollar tokens)
    # because Template.substitute is single-pass.
    assert "$today" not in stage5_prompt
    assert "$repo_name" not in stage5_prompt
    assert "$head_sha" not in stage5_prompt
    assert "$wiki_root" not in stage5_prompt
    assert "[2026-05-12] bootstrap-5 | project-a" in stage5_prompt
    assert _HEAD_SHA in stage5_prompt


def test_stage_5_runs_when_plans_dir_present(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=_clone(tmp_path, with_plans=True))

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(5,),
        clock=_fixed_clock(),
    )

    stage5_prompt = runner.calls[0].prompt
    assert "Plan / todo / decision sources were detected" in stage5_prompt
    assert "skipped (no plans/todos found)" not in stage5_prompt
    assert "$today" not in stage5_prompt
    assert "$repo_name" not in stage5_prompt
    assert "$head_sha" not in stage5_prompt
    assert "$wiki_root" not in stage5_prompt
    assert "[2026-05-12] bootstrap-5 | project-a" in stage5_prompt
    assert _HEAD_SHA in stage5_prompt


def test_cost_aggregated_across_stages(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    costs = (0.01, 0.02, 0.04, 0.08, 0.16)

    def _side_effect(cost: float) -> Callable[[Path], ClaudeResult]:
        def _run(cwd: Path) -> ClaudeResult:
            return _success_result(cost_usd=cost)

        return _run

    runner = _make_runner(side_effects=[_side_effect(c) for c in costs])
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.cost_usd_total == pytest.approx(sum(costs))


def test_frontmatter_violation_marks_stage_failed_continues(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_bad_page(cwd: Path) -> ClaudeResult:
        (cwd / "projects").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a" / "bad.md").write_text("no frontmatter\n", encoding="utf-8")
        return _success_result()

    def write_good_page(cwd: Path) -> ClaudeResult:
        (cwd / "projects").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a" / "good.md").write_text(_VALID_PAGE, encoding="utf-8")
        return _success_result()

    runner = _make_runner(
        side_effects=[
            write_bad_page,
            write_good_page,
            lambda cwd: _success_result(),
            lambda cwd: _success_result(),
            lambda cwd: _success_result(),
        ]
    )
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.stages_run == (1, 2, 3, 4, 5)
    assert outcome.stages_failed == (1,)
    assert not (wiki / "projects" / "project-a" / "bad.md").exists()
    assert (wiki / "projects" / "project-a" / "good.md").is_file()
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | project-a" in log_text
    assert "bootstrap-1" in log_text


def test_head_sha_taken_from_clone_result(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    head_sha = "9" * 40
    clone = CloneResult(
        path=_make_repo_path(tmp_path),
        head_sha=head_sha,
        was_initial_clone=False,
    )
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=clone)

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.head_sha == head_sha
    for call in runner.calls:
        assert head_sha in call.prompt


def test_per_repo_model_override(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    run_bootstrap(
        repo_cfg=_repo_cfg(model="claude-opus-4-7"),
        config=_config(model="claude-sonnet-4-6"),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 5
    for call in runner.calls:
        assert call.model == "claude-opus-4-7"
        assert call.allowed_tools == WIKI_READWRITE


def test_successful_stage_creates_wiki_commit(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_page(cwd: Path) -> ClaudeResult:
        (cwd / "projects").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a" / "data-model.md").write_text(_VALID_PAGE, encoding="utf-8")
        return _success_result()

    runner = _make_runner(
        side_effects=[
            write_page,
            lambda cwd: _success_result(),
            lambda cwd: _success_result(),
            lambda cwd: _success_result(),
            lambda cwd: _success_result(),
        ]
    )
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.stages_failed == ()
    head_after = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head_after != head_before
    last_subject = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert last_subject == f"cadence-memory: bootstrap-1 project-a {_SHORT_SHA}"
    assert outcome.pages_touched_total >= 1


def test_bootstrap_preserves_user_dirty_file_on_stage_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "config.yaml").write_text("model: old\n", encoding="utf-8")
    _git("add", "config.yaml", cwd=wiki)
    _git("commit", "-m", "add config", cwd=wiki)
    (wiki / "config.yaml").write_text("model: user-edit\n", encoding="utf-8")

    runner = _make_runner(
        side_effects=[
            lambda cwd: ClaudeResult(
                success=False,
                final_text="",
                cost_usd=None,
                duration_ms=None,
                tool_call_count=0,
                error="stage-1 boom",
            )
        ]
    )
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(1,),
        clock=_fixed_clock(),
    )

    assert 1 in outcome.stages_failed
    assert (wiki / "config.yaml").read_text(encoding="utf-8") == "model: user-edit\n"


def test_bootstrap_preserves_user_dirty_file_on_frontmatter_error(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "config.yaml").write_text("model: old\n", encoding="utf-8")
    _git("add", "config.yaml", cwd=wiki)
    _git("commit", "-m", "add config", cwd=wiki)
    (wiki / "config.yaml").write_text("model: user-edit\n", encoding="utf-8")

    def write_bad_page(cwd: Path) -> ClaudeResult:
        (cwd / "projects").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a" / "bad.md").write_text("no frontmatter\n", encoding="utf-8")
        return _success_result()

    runner = _make_runner(side_effects=[write_bad_page])
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(1,),
        clock=_fixed_clock(),
    )

    assert 1 in outcome.stages_failed
    assert not (wiki / "projects" / "project-a" / "bad.md").exists()
    assert (wiki / "config.yaml").read_text(encoding="utf-8") == "model: user-edit\n"


def test_bootstrap_reverts_claude_changes_on_runner_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_then_fail(cwd: Path) -> ClaudeResult:
        (cwd / "claude_new.md").write_text("claude output\n", encoding="utf-8")
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=None,
            duration_ms=None,
            tool_call_count=1,
            error="stage-1 boom",
        )

    runner = _make_runner(side_effects=[write_then_fail])
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(1,),
        clock=_fixed_clock(),
    )

    assert 1 in outcome.stages_failed
    assert not (wiki / "claude_new.md").exists()


def test_bootstrap_budget_usd_config_not_forwarded_to_runner(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=_clone(tmp_path))

    run_bootstrap(
        repo_cfg=_repo_cfg(budget_usd=1.25),
        config=_config(budget_usd=0.5),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(1,),
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 1
    assert all("budget_usd" not in kw for kw in runner.extra_kwargs)


@dataclass
class _RecordingLogger:
    events: list[ProgressEvent] = field(default_factory=list)

    @property
    def path(self) -> str | None:
        return None

    def print(self, fmt: str, *args: object) -> None:
        pass

    def info(self, fmt: str, *args: object) -> None:
        pass

    def warn(self, fmt: str, *args: object) -> None:
        pass

    def error(self, fmt: str, *args: object) -> None:
        pass

    def section(self, label: str) -> None:
        pass

    def log_event(self, event: ProgressEvent) -> None:
        self.events.append(event)


def test_bootstrap_emits_phase_and_stage_events(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(clone_result=_clone(tmp_path))
    recording = _RecordingLogger()

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(1, 2),
        clock=_fixed_clock(),
        logger=recording,
    )

    starts = [e for e in recording.events if isinstance(e, PhaseStartEvent)]
    ends = [e for e in recording.events if isinstance(e, PhaseEndEvent)]
    stage_starts = [e for e in recording.events if isinstance(e, StageStartEvent)]
    stage_ends = [e for e in recording.events if isinstance(e, StageEndEvent)]

    assert len(starts) == 1
    assert starts[0].phase == "bootstrap"
    assert starts[0].repo == "project-a"
    assert len(ends) == 1
    assert ends[0].phase == "bootstrap"
    assert ends[0].result == "ok"
    assert len(stage_starts) == 2
    assert stage_starts[0].stage_index == 1
    assert stage_starts[1].stage_index == 2
    assert len(stage_ends) == 2
    assert all(e.result == "ok" for e in stage_ends)


def test_bootstrap_emits_error_event_on_runner_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner(
        side_effects=[
            lambda cwd: ClaudeResult(
                success=False,
                final_text="",
                cost_usd=None,
                duration_ms=None,
                tool_call_count=0,
                error="stage-1 exploded",
            )
        ]
    )
    cache = _FakeGitCache(clone_result=_clone(tmp_path))
    recording = _RecordingLogger()

    from cadence_memory.progress.events import ErrorEvent

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(1,),
        clock=_fixed_clock(),
        logger=recording,
    )

    errors = [e for e in recording.events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert "stage-1 exploded" in errors[0].message
    stage_ends = [e for e in recording.events if isinstance(e, StageEndEvent)]
    assert stage_ends[0].result == "failed"


def test_bootstrap_emits_error_event_on_frontmatter_violation(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_bad_page(cwd: Path) -> ClaudeResult:
        (cwd / "projects").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a" / "bad.md").write_text("no frontmatter\n", encoding="utf-8")
        return _success_result()

    runner = _make_runner(side_effects=[write_bad_page])
    cache = _FakeGitCache(clone_result=_clone(tmp_path))
    recording = _RecordingLogger()

    from cadence_memory.progress.events import ErrorEvent

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=cache,
        runner=runner,
        stages=(1,),
        clock=_fixed_clock(),
        logger=recording,
    )

    errors = [e for e in recording.events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].message == "frontmatter error"
    stage_ends = [e for e in recording.events if isinstance(e, StageEndEvent)]
    assert stage_ends[0].result == "failed"
