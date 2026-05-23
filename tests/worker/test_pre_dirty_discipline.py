"""Consolidated pre_dirty discipline contract (task 1041).

One boundary-by-boundary contract pinning that the worker pipeline never
sweeps a user's pre-existing dirty file into a revert, a validation failure,
or a failure commit. Each test maps to one guarantee:

- ``revert_wiki(preserve=...)`` leaves pre-dirty files untouched (1028).
- the validation loop skips pre-dirty files (1037).
- the failure commit stages only ``log.md`` and excludes pre-dirty files (1037).
- bootstrap recaptures pre_dirty at the start of each stage, so once a stage
  commits a previously user-dirty file, a later failing stage's revert restores
  it instead of preserving Claude's new edit (a single up-front snapshot would
  leak the edit).
- the documented, accepted success path: ``stage_and_commit``'s ``git add -A``
  DOES commit a pre-existing user edit (pinned so a future change is caught).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cadence_memory.config.schema import Config, RepoConfig, WorkerConfig
from cadence_memory.executor.runner import ClaudeResult
from cadence_memory.git.cache import CloneResult, CommitInfo
from cadence_memory.git.walker import SingleCommitEvent
from cadence_memory.worker.bootstrap import run_bootstrap
from cadence_memory.worker.ingest import ingest_event
from cadence_memory.worker.wiki_commit import list_touched_paths, revert_wiki


@dataclass
class _FakeClaudeRunner:
    """Mock ClaudeRunner: each call runs the next queued side-effect on the wiki dir.

    The side-effect queue is sized to the exact number of ``run`` calls each
    scenario expects, so an extra call (e.g. if the pipeline grows a stage or a
    diff shard) pops an empty list and fails loudly instead of silently
    succeeding and masking the regression these contracts exist to catch.
    """

    side_effects: list[Callable[[Path], ClaudeResult]] = field(default_factory=list)

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
        assert cwd is not None
        side_effect = self.side_effects.pop(0)
        return side_effect(cwd)


@dataclass
class _FakeGitCache:
    """Mock GitCache covering the ingest (diff/changed_files) and bootstrap (ensure) paths."""

    clone_result: CloneResult | None = None

    def diff(self, *, name: str, sha: str) -> str:
        return "diff --git a/file b/file\n+x\n"

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:
        return ("file.py",)

    def ensure(self, *, name: str, url: str, branch: str) -> CloneResult:
        assert self.clone_result is not None
        return self.clone_result

    def head(self, *, name: str, branch: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:  # pragma: no cover - unused
        raise NotImplementedError

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:  # pragma: no cover - unused
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


def _commit(sha: str = "a" * 40, subject: str = "do thing") -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject=subject,
        body="body details",
        author_date_iso="2026-05-12T12:00:00+00:00",
        parents=(),
    )


def _single_event() -> SingleCommitEvent:
    return SingleCommitEvent(repo_name="project-a", commit=_commit())


def _repo_cfg() -> RepoConfig:
    return RepoConfig(
        name="project-a",
        url="https://example.com/project-a.git",
        branch="main",
    )


def _config() -> Config:
    return Config(
        model="claude-sonnet-4-6",
        idle_timeout_s=300,
        worker=WorkerConfig(),
        repos=(),
    )


def _fixed_clock() -> Callable[[], datetime]:
    return lambda: datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


def _clone(tmp_path: Path) -> CloneResult:
    src = tmp_path / "src-repo"
    src.mkdir()
    return CloneResult(path=src, head_sha="f" * 40, was_initial_clone=False)


def _seed_user_dirty_tracked(wiki: Path) -> None:
    """Commit a user file, then leave a local edit so it shows as pre-dirty."""
    (wiki / "config.yaml").write_text("model: old\n", encoding="utf-8")
    _git("add", "config.yaml", cwd=wiki)
    _git("commit", "-m", "add config", cwd=wiki)
    (wiki / "config.yaml").write_text("model: user-edit\n", encoding="utf-8")


# --- revert preserves pre-dirty (1028) -------------------------------------


def test_revert_preserves_pre_dirty_user_files(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    # User edits a tracked file and drops an untracked file before the worker runs.
    (wiki / "index.md").write_text("user edit\n", encoding="utf-8")
    (wiki / "raw" / "notes").mkdir(parents=True)
    (wiki / "raw" / "notes" / "draft.md").write_text("draft\n", encoding="utf-8")
    pre_dirty = frozenset(list_touched_paths(wiki))

    # Claude then makes its own change that must be discarded on revert.
    (wiki / "claude_stub.md").write_text("claude output\n", encoding="utf-8")

    revert_wiki(wiki, preserve=pre_dirty)

    assert (wiki / "index.md").read_text(encoding="utf-8") == "user edit\n"
    assert (wiki / "raw" / "notes" / "draft.md").read_text(encoding="utf-8") == "draft\n"
    assert not (wiki / "claude_stub.md").exists()


# --- validation skips pre-dirty (1037) -------------------------------------


def test_validation_skips_pre_dirty_invalid_frontmatter(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    # User's own markdown has invalid frontmatter and is dirty before the run.
    (wiki / "CLAUDE.md").write_text("user notes, no frontmatter\n", encoding="utf-8")

    def write_good(cwd: Path) -> ClaudeResult:
        (cwd / "projects" / "project-a").mkdir(parents=True, exist_ok=True)
        (cwd / "projects" / "project-a" / "overview.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.03,
            duration_ms=400,
            tool_call_count=2,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_good])

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=_FakeGitCache(),
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.wiki_commit_sha is not None
    assert "FAILED ingest" not in (wiki / "log.md").read_text(encoding="utf-8")


# --- failure commit excludes pre-dirty (1037) ------------------------------


def test_failure_commit_excludes_pre_dirty(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _seed_user_dirty_tracked(wiki)

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

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=_FakeGitCache(),
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    # (a) the user's file content is intact.
    assert (wiki / "config.yaml").read_text(encoding="utf-8") == "model: user-edit\n"
    # (b) the failure commit staged only log.md.
    committed = _git("show", "--name-only", "--format=", "HEAD", cwd=wiki).stdout.split()
    assert committed == ["log.md"]
    # ...and the user's file is still dirty in the working tree.
    status = _git("status", "--porcelain", cwd=wiki).stdout
    assert "config.yaml" in status


# --- bootstrap stages do not leak ------------------------------------------


def test_bootstrap_recaptures_pre_dirty_each_stage(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _seed_user_dirty_tracked(wiki)

    def fail_with_scratch(cwd: Path) -> ClaudeResult:
        (cwd / "scratch.md").write_text("partial claude work\n", encoding="utf-8")
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.01,
            duration_ms=50,
            tool_call_count=1,
            error="stage boom",
        )

    def write_page_two(cwd: Path) -> ClaudeResult:
        (cwd / "projects" / "project-a").mkdir(parents=True, exist_ok=True)
        (cwd / "projects" / "project-a" / "page-two.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.02,
            duration_ms=100,
            tool_call_count=1,
            error=None,
        )

    def fail_after_touching_user_file(cwd: Path) -> ClaudeResult:
        # Stage 2 committed config.yaml via ``git add -A``, so it is clean at the
        # start of this stage. Re-editing it here must be reverted — which only
        # happens if pre_dirty was recaptured this stage (an empty set), not
        # carried over from stage 1 (where config.yaml was still dirty).
        (cwd / "config.yaml").write_text("model: claude-edit\n", encoding="utf-8")
        (cwd / "scratch.md").write_text("partial claude work\n", encoding="utf-8")
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.01,
            duration_ms=50,
            tool_call_count=1,
            error="stage boom",
        )

    # Stage 1 fails (its revert must preserve the user's still-dirty edit),
    # stage 2 succeeds and commits config.yaml + page-two.md, stage 3 fails after
    # re-editing the now-committed config.yaml.
    runner = _FakeClaudeRunner(
        side_effects=[fail_with_scratch, write_page_two, fail_after_touching_user_file]
    )

    outcome = run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=_FakeGitCache(clone_result=_clone(tmp_path)),
        runner=runner,
        stages=(1, 2, 3),
        clock=_fixed_clock(),
    )

    assert outcome.stages_failed == (1, 3)
    # Per-stage recapture: stage 3's revert restored config.yaml to the value
    # stage 2 committed instead of preserving Claude's stage-3 edit. A single
    # up-front pre_dirty snapshot would have kept "model: claude-edit\n" here.
    assert (wiki / "config.yaml").read_text(encoding="utf-8") == "model: user-edit\n"
    # A page committed by an earlier successful stage survives a later revert.
    assert (wiki / "projects" / "project-a" / "page-two.md").is_file()
    # Each failing stage's scratch output was reverted.
    assert not (wiki / "scratch.md").exists()


# --- accepted success-path behavior ----------------------------------------


def test_success_commit_includes_pre_dirty_user_edit(tmp_path: Path) -> None:
    """Accepted trade-off: the success path's ``git add -A`` sweeps in a user edit.

    Pinned so a future switch to path-scoped success staging is caught.
    """
    wiki = _init_wiki(tmp_path)
    _seed_user_dirty_tracked(wiki)

    def write_page(cwd: Path) -> ClaudeResult:
        (cwd / "projects" / "project-a").mkdir(parents=True, exist_ok=True)
        (cwd / "projects" / "project-a" / "overview.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.03,
            duration_ms=400,
            tool_call_count=2,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_page])

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=_FakeGitCache(),
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    committed = _git("show", "--name-only", "--format=", "HEAD", cwd=wiki).stdout.split()
    assert "config.yaml" in committed
    assert "projects/project-a/overview.md" in committed
    # The user edit is now committed (no longer dirty).
    assert "config.yaml" not in _git("status", "--porcelain", cwd=wiki).stdout
