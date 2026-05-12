"""Tests for the ingest orchestrator (design2 §7)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from string import Template

import pytest

from cadence_memory.config.schema import Config, RepoConfig, WorkerConfig
from cadence_memory.executor.runner import ClaudeResult
from cadence_memory.executor.tool_sets import WIKI_READWRITE
from cadence_memory.git.cache import CommitInfo
from cadence_memory.git.walker import NoiseBatchEvent, SingleCommitEvent
from cadence_memory.worker.ingest import (
    _load_default_template,
    ingest_event,
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
    """Mock ClaudeRunner that records call kwargs and lets each call mutate the wiki dir."""

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


@dataclass
class _FakeGitCache:
    """Mock GitCache returning pre-baked changed_files / diff output."""

    diffs: dict[str, str] = field(default_factory=dict)
    changed: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def diff(self, *, name: str, sha: str) -> str:
        return self.diffs.get(sha, "diff --git a/file b/file\n+x\n")

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:
        return self.changed.get(sha, ("file.py",))

    def head(self, *, name: str, branch: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:  # pragma: no cover - unused
        raise NotImplementedError

    def ensure(self, *, name: str, url: str, branch: str):  # pragma: no cover - unused
        raise NotImplementedError

    def show_commit(self, *, name: str, sha: str):  # pragma: no cover - unused
        raise NotImplementedError

    def list_commits(self, *, name, since_sha, branch, reverse=True):  # pragma: no cover
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


def _commit(sha: str = "a" * 40, subject: str = "do thing") -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject=subject,
        body="body details",
        author_date_iso="2026-05-12T12:00:00+00:00",
        parents=(),
    )


def _single_event(commit: CommitInfo | None = None) -> SingleCommitEvent:
    return SingleCommitEvent(repo_name="project-a", commit=commit or _commit())


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


def test_prompt_template_contains_all_placeholders() -> None:
    text = _load_default_template()
    for token in (
        "$repo_name",
        "$commit_subject",
        "$commit_body",
        "$short_sha",
        "$changed_files",
        "$diff",
        "$index_head",
        "$log_tail",
        "$wiki_root",
    ):
        assert token in text


def test_renders_prompt_with_all_placeholders(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner(
        side_effects=[
            lambda cwd: ClaudeResult(
                success=True,
                final_text="ok",
                cost_usd=0.02,
                duration_ms=200,
                tool_call_count=0,
                error=None,
            )
        ]
    )
    cache = _FakeGitCache(
        diffs={"a" * 40: "diff --git a/src/x.py b/src/x.py\n+y\n"},
        changed={"a" * 40: ("src/x.py",)},
    )

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success
    template_text = _load_default_template()
    identifiers = set(Template(template_text).get_identifiers())
    rendered = runner.calls[0].prompt
    for ident in identifiers:
        assert f"${ident}" not in rendered, f"placeholder {ident!r} not substituted"


def test_happy_path_commits_wiki(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_page(cwd: Path) -> ClaudeResult:
        (cwd / "projects").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a").mkdir(exist_ok=True)
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
    cache = _FakeGitCache()

    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.wiki_commit_sha is not None
    head_after = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head_after != head_before
    subject_line = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert subject_line.startswith("cadence-memory: ingest project-a")
    assert outcome.cost_usd == 0.03
    assert outcome.duration_ms == 400


def test_no_op_commit_skipped(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache()

    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    commit = _commit()

    outcome = ingest_event(
        event=_single_event(commit),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.wiki_commit_sha is None
    assert outcome.head_sha == commit.sha
    head_after = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head_after == head_before


def test_failure_appends_log_entry(tmp_path: Path) -> None:
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
    cache = _FakeGitCache()

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.error == "boom"
    assert outcome.wiki_commit_sha is None
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | project-a" in log_text
    assert "boom" in log_text
    last_subject = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert last_subject.startswith("cadence-memory: ingest failure project-a")


def test_invalid_frontmatter_reverts(tmp_path: Path) -> None:
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
    cache = _FakeGitCache()

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.wiki_commit_sha is None
    assert outcome.error is not None
    assert not (wiki / "stub.md").exists()
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | project-a" in log_text


def test_head_sha_populated_on_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner(
        side_effects=[
            lambda cwd: ClaudeResult(
                success=False,
                final_text="",
                cost_usd=None,
                duration_ms=None,
                tool_call_count=0,
                error="x",
            )
        ]
    )
    cache = _FakeGitCache()
    commit = _commit()

    outcome = ingest_event(
        event=_single_event(commit),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.head_sha == commit.sha
    assert outcome.pages_touched == ()
    assert outcome.wiki_commit_sha is None


def test_per_repo_model_override(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache()

    ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(model="claude-opus-4-7"),
        config=_config(model="claude-sonnet-4-6"),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert runner.calls[0].model == "claude-opus-4-7"
    assert runner.calls[0].allowed_tools == WIKI_READWRITE


def test_per_repo_budget_override(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache()

    ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(budget_usd=1.25),
        config=_config(budget_usd=0.5),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert runner.calls[0].budget_usd == 1.25


def test_noise_batch_event_uses_aggregated_subject(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    commits = (
        _commit(sha="b" * 40, subject="docs: minor"),
        _commit(sha="c" * 40, subject="chore: bump"),
        _commit(sha="d" * 40, subject="ci: tweak"),
    )
    event = NoiseBatchEvent(repo_name="project-a", commits=commits)
    cache = _FakeGitCache(
        diffs={
            "b" * 40: "diff --git a/docs/x b/docs/x\n+x\n",
            "c" * 40: "diff --git a/pkg.toml b/pkg.toml\n+x\n",
            "d" * 40: "diff --git a/.github/x b/.github/x\n+x\n",
        }
    )
    runner = _FakeClaudeRunner()

    outcome = ingest_event(
        event=event,
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    prompt = runner.calls[0].prompt
    assert "3 noise commits" in prompt
    assert "# Commit bbbbbbb — docs: minor" in prompt
    assert "# Commit ccccccc — chore: bump" in prompt
    assert "# Commit ddddddd — ci: tweak" in prompt
    assert outcome.head_sha == "d" * 40


def test_huge_diff_is_sharded(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    src_block = (
        "diff --git a/src/big.py b/src/big.py\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/src/big.py\n"
        "+++ b/src/big.py\n"
        "@@ -0,0 +1,1 @@\n" + ("+x" * 130_000) + "\n"
    )
    tests_block = (
        "diff --git a/tests/big.py b/tests/big.py\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/tests/big.py\n"
        "+++ b/tests/big.py\n"
        "@@ -0,0 +1,1 @@\n" + ("+y" * 130_000) + "\n"
    )
    huge_diff = src_block + tests_block
    cache = _FakeGitCache(diffs={"a" * 40: huge_diff})
    runner = _FakeClaudeRunner()

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert len(runner.calls) >= 2
    assert "[shard 1 of 2 — do NOT append to log.md]" in runner.calls[0].prompt
    final_prompt = runner.calls[-1].prompt
    assert "do NOT append to log.md" not in final_prompt
    assert outcome.head_sha == "a" * 40


def test_custom_clock_used_for_log_date(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def fail(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=None,
            duration_ms=None,
            tool_call_count=0,
            error="something",
        )

    runner = _FakeClaudeRunner(side_effects=[fail])
    cache = _FakeGitCache()

    ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=lambda: datetime(2026, 5, 12, 9, 0, 0, tzinfo=UTC),
    )

    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "[2026-05-12]" in log_text


def test_prompt_template_override(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache()

    ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        prompt_template="$repo_name|$short_sha",
        clock=_fixed_clock(),
    )

    expected_short = ("a" * 40)[:7]
    assert runner.calls[0].prompt == f"project-a|{expected_short}"


def test_noop_when_template_override_blank_diff(tmp_path: Path) -> None:
    """Override + a runner that writes nothing should still succeed with no wiki commit."""
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache(diffs={"a" * 40: ""})

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        prompt_template="static",
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.wiki_commit_sha is None


def test_runner_receives_idle_timeout_and_cwd(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache()

    ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(idle_timeout_s=600),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert runner.calls[0].idle_timeout_s == 600
    assert runner.calls[0].cwd == wiki


def test_subject_truncation_in_commit_message(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_page(cwd: Path) -> ClaudeResult:
        (cwd / "page.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.01,
            duration_ms=10,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_page])
    cache = _FakeGitCache()
    long_subject = "x" * 200

    outcome = ingest_event(
        event=_single_event(_commit(subject=long_subject)),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    subject_line = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    truncated = "x" * 72
    assert truncated in subject_line
    assert ("x" * 73) not in subject_line


def _huge_two_dir_diff() -> str:
    src_block = (
        "diff --git a/src/big.py b/src/big.py\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/src/big.py\n"
        "+++ b/src/big.py\n"
        "@@ -0,0 +1,1 @@\n" + ("+x" * 130_000) + "\n"
    )
    tests_block = (
        "diff --git a/tests/big.py b/tests/big.py\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/tests/big.py\n"
        "+++ b/tests/big.py\n"
        "@@ -0,0 +1,1 @@\n" + ("+y" * 130_000) + "\n"
    )
    return src_block + tests_block


def test_runner_failure_reverts_partial_writes(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def partial_write_then_fail(cwd: Path) -> ClaudeResult:
        (cwd / "partial.md").write_text("incomplete\n", encoding="utf-8")
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.01,
            duration_ms=50,
            tool_call_count=1,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[partial_write_then_fail])
    cache = _FakeGitCache()

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert not (wiki / "partial.md").exists()
    head_subject = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert head_subject == "cadence-memory: ingest failure project-a aaaaaaa"
    show = _git("show", "--name-only", "--format=", "HEAD", cwd=wiki).stdout.strip()
    assert show == "log.md"


def test_multi_shard_failure_discards_earlier_shard_writes(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_shard_one_page(cwd: Path) -> ClaudeResult:
        (cwd / "projects").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a" / "first.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.02,
            duration_ms=100,
            tool_call_count=1,
            error=None,
        )

    def fail_shard_two(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.03,
            duration_ms=200,
            tool_call_count=0,
            error="shard 2 boom",
        )

    runner = _FakeClaudeRunner(side_effects=[write_shard_one_page, fail_shard_two])
    cache = _FakeGitCache(diffs={"a" * 40: _huge_two_dir_diff()})

    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.error == "shard 2 boom"
    assert outcome.wiki_commit_sha is None
    assert not (wiki / "projects" / "project-a" / "first.md").exists()
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | project-a" in log_text
    assert "shard 2 boom" in log_text
    history = _git("rev-list", f"{head_before}..HEAD", cwd=wiki).stdout.strip().splitlines()
    assert len(history) == 1
    assert outcome.cost_usd == pytest.approx(0.05)
    assert outcome.duration_ms == 300


def test_multi_shard_happy_path_single_commit_aggregates_outcome(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    def write_shard_one(cwd: Path) -> ClaudeResult:
        (cwd / "projects").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a").mkdir(exist_ok=True)
        (cwd / "projects" / "project-a" / "first.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.02,
            duration_ms=100,
            tool_call_count=1,
            error=None,
        )

    def write_shard_two(cwd: Path) -> ClaudeResult:
        (cwd / "projects" / "project-a" / "second.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.04,
            duration_ms=200,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_shard_one, write_shard_two])
    cache = _FakeGitCache(diffs={"a" * 40: _huge_two_dir_diff()})

    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.wiki_commit_sha is not None
    history = _git("rev-list", f"{head_before}..HEAD", cwd=wiki).stdout.strip().splitlines()
    assert len(history) == 1
    files_in_commit = _git("show", "--name-only", "--format=", "HEAD", cwd=wiki).stdout.strip()
    assert "projects/project-a/first.md" in files_in_commit
    assert "projects/project-a/second.md" in files_in_commit
    touched_rel = {p.relative_to(wiki.resolve()) for p in outcome.pages_touched}
    assert Path("projects/project-a/first.md") in touched_rel
    assert Path("projects/project-a/second.md") in touched_rel
    assert outcome.cost_usd == pytest.approx(0.06)
    assert outcome.duration_ms == 300


def test_no_op_pages_touched_is_empty(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    runner = _FakeClaudeRunner()
    cache = _FakeGitCache()

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.pages_touched == ()


def test_huge_noise_batch_shards_per_commit(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    big_src_diff = (
        "diff --git a/src/big1.py b/src/big1.py\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/src/big1.py\n"
        "+++ b/src/big1.py\n"
        "@@ -0,0 +1,1 @@\n" + ("+x" * 130_000) + "\n"
    )
    big_tests_diff = (
        "diff --git a/tests/big2.py b/tests/big2.py\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/tests/big2.py\n"
        "+++ b/tests/big2.py\n"
        "@@ -0,0 +1,1 @@\n" + ("+y" * 130_000) + "\n"
    )
    commits = (
        _commit(sha="b" * 40, subject="docs: huge"),
        _commit(sha="c" * 40, subject="chore: huge"),
    )
    event = NoiseBatchEvent(repo_name="project-a", commits=commits)
    cache = _FakeGitCache(
        diffs={
            "b" * 40: big_src_diff,
            "c" * 40: big_tests_diff,
        }
    )
    runner = _FakeClaudeRunner()

    outcome = ingest_event(
        event=event,
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert len(runner.calls) == 2
    first_prompt = runner.calls[0].prompt
    second_prompt = runner.calls[1].prompt
    assert "# Commit bbbbbbb — docs: huge" in first_prompt
    assert "# Commit ccccccc — chore: huge" not in first_prompt
    assert "# Commit ccccccc — chore: huge" in second_prompt
    assert "# Commit bbbbbbb — docs: huge" not in second_prompt
    assert "[shard 1 of 2 — do NOT append to log.md]" in first_prompt
    assert "do NOT append to log.md" not in second_prompt
    assert outcome.head_sha == "c" * 40


@pytest.mark.parametrize("fname", ["index.md", "log.md"])
def test_missing_seed_files_yield_empty_context(tmp_path: Path, fname: str) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / fname).unlink()
    _git("add", "-A", cwd=wiki)
    _git("commit", "-m", "drop", cwd=wiki)

    runner = _FakeClaudeRunner()
    cache = _FakeGitCache()

    outcome = ingest_event(
        event=_single_event(),
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        cache=cache,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
