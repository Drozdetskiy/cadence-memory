"""Tests for the `run_pending` orchestrator (design2 §6.1)."""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cadence_memory.config.schema import Config, RepoConfig, WorkerConfig
from cadence_memory.executor.runner import ClaudeResult
from cadence_memory.git.cache import CloneResult, CommitInfo
from cadence_memory.git.errors import GitError, HistoryRewrittenError
from cadence_memory.git.walker import IngestEvent, NoiseBatchEvent, SingleCommitEvent
from cadence_memory.worker.run import RunSummary, run_pending
from cadence_memory.worker.state import RepoState, WorkerState

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
            duration_ms=10,
            tool_call_count=0,
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
            effect = self.side_effects.pop(0)
            assert cwd is not None
            return effect(cwd)
        return self.default_result


@dataclass
class _FakeGitCache:
    commits_by_repo: dict[str, tuple[CommitInfo, ...]] = field(default_factory=dict)
    ensure_calls: list[tuple[str, str, str]] = field(default_factory=list)
    ensure_errors: dict[str, Exception] = field(default_factory=dict)
    diffs: dict[str, str] = field(default_factory=dict)
    changed: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def ensure(self, *, name: str, url: str, branch: str) -> CloneResult:
        self.ensure_calls.append((name, url, branch))
        if name in self.ensure_errors:
            raise self.ensure_errors[name]
        return CloneResult(
            path=Path("/tmp/fake") / name,
            head_sha="head",
            was_initial_clone=False,
        )

    def head(self, *, name: str, branch: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:  # pragma: no cover - unused
        raise NotImplementedError

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:  # pragma: no cover - unused
        raise NotImplementedError

    def diff(self, *, name: str, sha: str) -> str:
        return self.diffs.get(sha, "diff --git a/file b/file\n+x\n")

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:
        return self.changed.get(sha, ("file.py",))

    def list_commits(
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]:
        return self.commits_by_repo.get(name, ())


def _commit(sha: str, subject: str = "do thing") -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject=subject,
        body="",
        author_date_iso="2026-05-12T12:00:00+00:00",
        parents=(),
    )


def _repo_cfg(name: str = "project-a", **overrides: object) -> RepoConfig:
    base: dict[str, object] = {
        "name": name,
        "url": f"https://example.com/{name}.git",
        "branch": "main",
    }
    base.update(overrides)
    return RepoConfig(**base)  # type: ignore[arg-type]


def _config(*, repos: tuple[RepoConfig, ...] = (), **overrides: object) -> Config:
    base: dict[str, object] = {
        "model": "claude-sonnet-4-6",
        "budget_usd": 0.5,
        "idle_timeout_s": 300,
        "worker": WorkerConfig(),
        "repos": repos,
    }
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def _fixed_clock() -> Callable[[], datetime]:
    return lambda: datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


def _success(cwd: Path) -> ClaudeResult:
    return ClaudeResult(
        success=True,
        final_text="ok",
        cost_usd=0.02,
        duration_ms=100,
        tool_call_count=0,
        error=None,
    )


def _failure(error: str = "boom") -> Callable[[Path], ClaudeResult]:
    def _fail(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.01,
            duration_ms=10,
            tool_call_count=0,
            error=error,
        )

    return _fail


def test_run_processes_pending_events(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    commits = (_commit("a" * 40, "feat: one"), _commit("b" * 40, "feat: two"))
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 2
    assert summary.events_processed == 2
    assert summary.events_failed == 0
    assert summary.repos == ("project-a",)


def test_run_advances_state_on_success(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    sha = "a" * 40
    cache = _FakeGitCache(commits_by_repo={"project-a": (_commit(sha, "feat: x"),)})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))

    state, _summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    repo_state = state.repos["project-a"]
    assert repo_state.last_sha == sha
    assert repo_state.commits_processed == 1
    assert repo_state.last_failure is None
    assert repo_state.last_run_at is not None


def test_run_does_not_advance_state_on_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(commits_by_repo={"project-a": (_commit("a" * 40, "feat: x"),)})
    runner = _FakeClaudeRunner(side_effects=[_failure("nope")])
    config = _config(repos=(_repo_cfg(),))
    seeded = WorkerState(
        repos={"project-a": RepoState(last_sha="OLD" + "0" * 37, commits_processed=4)}
    )

    state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=seeded,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    repo_state = state.repos["project-a"]
    assert repo_state.last_sha == "OLD" + "0" * 37
    assert repo_state.commits_processed == 4
    assert repo_state.last_failure == "nope"
    assert summary.events_failed == 1
    assert summary.events_processed == 0


def test_run_stops_on_failure_by_default(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    commits = (
        _commit("a" * 40, "first"),
        _commit("b" * 40, "second"),
        _commit("c" * 40, "third"),
    )
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner(side_effects=[_failure("bad")])
    config = _config(repos=(_repo_cfg(),))

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 1
    assert summary.events_failed == 1
    assert summary.events_processed == 0


def test_run_continues_on_failure_when_disabled(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    commits = (
        _commit("a" * 40, "first"),
        _commit("b" * 40, "second"),
        _commit("c" * 40, "third"),
    )
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner(side_effects=[_failure("bad"), _success, _success])
    worker = WorkerConfig(stop_on_failure=False)
    config = _config(repos=(_repo_cfg(),), worker=worker)

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 3
    assert summary.events_failed == 1
    assert summary.events_processed == 2


def test_run_history_rewritten_records_failure_no_throw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(commits_by_repo={"b": (_commit("b" * 40, "feat: b"),)})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"), _repo_cfg("b")))

    def fake_iter(
        *,
        cache: object,
        repo_name: str,
        branch: str,
        since_sha: str | None,
        skip_patterns: tuple[str, ...],
        noise_patterns: tuple[str, ...],
        limit: int | None = None,
    ) -> Iterator[IngestEvent]:
        if repo_name == "a":
            raise HistoryRewrittenError("a was rewritten")
        yield SingleCommitEvent(repo_name="b", commit=_commit("b" * 40, "feat: b"))

    monkeypatch.setattr("cadence_memory.worker.run.iter_pending_commits", fake_iter)

    state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert state.repos["a"].last_failure == "a was rewritten"
    assert state.repos["a"].last_sha is None
    assert "b" in state.repos
    assert summary.events_processed == 1
    assert summary.repos == ("b",)


def test_run_only_filters_repos(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(commits_by_repo={"b": (_commit("b" * 40, "feat: b"),)})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"), _repo_cfg("b")))

    _state, _summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        only_repo="b",
        clock=_fixed_clock(),
    )

    assert [c[0] for c in cache.ensure_calls] == ["b"]


def test_run_limit_caps_events_single_repo(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    commits = tuple(_commit((chr(ord("a") + i) * 40), f"feat: {i}") for i in range(5))
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        limit=2,
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 2
    assert summary.events_processed == 2


def test_run_limit_caps_events_across_repos(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    repo_a_commits = tuple(_commit((chr(ord("a") + i) * 40), f"feat-a: {i}") for i in range(2))
    repo_b_commits = tuple(_commit((chr(ord("m") + i) * 40), f"feat-b: {i}") for i in range(5))
    cache = _FakeGitCache(commits_by_repo={"a": repo_a_commits, "b": repo_b_commits})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"), _repo_cfg("b")))

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        limit=3,
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 3
    assert summary.events_processed == 3


def test_run_dry_run_calls_no_runner(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    commits = (_commit("a" * 40, "feat: one"), _commit("b" * 40, "feat: two"))
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))
    out = io.StringIO()

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        dry_run=True,
        clock=_fixed_clock(),
        out=out,
    )

    assert runner.calls == []
    assert summary.events_processed == 0
    assert not (wiki / ".cadence-memory" / "state.json").exists()
    text = out.getvalue()
    assert "project-a: 2 pending events" in text
    assert "[single]" in text


def test_run_dry_run_plan_format(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    long_subject = "x" * 200
    commits = (_commit("a" * 40, long_subject),)
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))
    out = io.StringIO()

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        dry_run=True,
        clock=_fixed_clock(),
        out=out,
    )

    lines = out.getvalue().splitlines()
    assert lines[0] == "plan:"
    assert lines[-1].startswith("total:")
    assert "model=claude-sonnet-4-6" in lines[-1]
    assert "budget=$0.50/call" in lines[-1]
    event_line = next(line for line in lines if "[single]" in line)
    truncated = "x" * 72
    assert truncated in event_line
    assert ("x" * 73) not in event_line


def test_run_dry_run_budget_unset(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache()
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),), budget_usd=None)
    out = io.StringIO()

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        dry_run=True,
        clock=_fixed_clock(),
        out=out,
    )

    assert "budget=unset" in out.getvalue()


def test_run_persists_state_after_each_event(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    sha_a = "a" * 40
    sha_b = "b" * 40
    commits = (_commit(sha_a, "feat: one"), _commit(sha_b, "feat: two"))
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})

    seen_shas: list[str | None] = []

    def capture_then_succeed(cwd: Path) -> ClaudeResult:
        state_path = wiki / ".cadence-memory" / "state.json"
        if state_path.exists():
            data = json.loads(state_path.read_text(encoding="utf-8"))
            seen_shas.append(data["repos"].get("project-a", {}).get("last_sha"))
        else:
            seen_shas.append(None)
        return _success(cwd)

    runner = _FakeClaudeRunner(side_effects=[capture_then_succeed, capture_then_succeed])
    config = _config(repos=(_repo_cfg(),))

    _state, _summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    state_path = wiki / ".cadence-memory" / "state.json"
    assert state_path.exists()
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert on_disk["repos"]["project-a"]["last_sha"] == sha_b
    assert on_disk["repos"]["project-a"]["commits_processed"] == 2
    assert seen_shas == [None, sha_a]


def test_run_summary_includes_costs(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    commits = (_commit("a" * 40, "feat: one"), _commit("b" * 40, "feat: two"))
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner(side_effects=[_success, _success])
    config = _config(repos=(_repo_cfg(),))

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert summary.cost_usd_total == pytest.approx(0.04)


def test_run_dry_run_shows_noise_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache()
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))
    out = io.StringIO()

    noise = NoiseBatchEvent(
        repo_name="project-a",
        commits=(
            _commit("a" * 40, "chore(deps): bump a"),
            _commit("b" * 40, "chore(deps): bump b"),
        ),
    )

    def fake_iter(
        *,
        cache: object,
        repo_name: str,
        branch: str,
        since_sha: str | None,
        skip_patterns: tuple[str, ...],
        noise_patterns: tuple[str, ...],
        limit: int | None = None,
    ) -> Iterator[IngestEvent]:
        yield noise

    monkeypatch.setattr("cadence_memory.worker.run.iter_pending_commits", fake_iter)

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        dry_run=True,
        clock=_fixed_clock(),
        out=out,
    )

    text = out.getvalue()
    assert "[noise:2]" in text
    assert "bbbbbbb" in text
    assert "chore(deps): bump b" in text


def test_run_uses_start_commit_when_no_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache()
    runner = _FakeClaudeRunner()
    start = "deadbee" + "0" * 33
    config = _config(repos=(_repo_cfg(start_commit=start),))

    captured: dict[str, object] = {}

    def fake_iter(
        *,
        cache: object,
        repo_name: str,
        branch: str,
        since_sha: str | None,
        skip_patterns: tuple[str, ...],
        noise_patterns: tuple[str, ...],
        limit: int | None = None,
    ) -> Iterator[IngestEvent]:
        captured["since_sha"] = since_sha
        return iter(())

    monkeypatch.setattr("cadence_memory.worker.run.iter_pending_commits", fake_iter)

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert captured["since_sha"] == start


def test_run_clears_last_failure_on_success(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    sha = "a" * 40
    cache = _FakeGitCache(commits_by_repo={"project-a": (_commit(sha, "feat: x"),)})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))
    seeded = WorkerState(repos={"project-a": RepoState(last_failure="old boom")})

    state, _summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=seeded,
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert state.repos["project-a"].last_failure is None
    assert state.repos["project-a"].last_sha == sha


def test_run_respects_max_commits_per_run(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    commits = tuple(_commit((chr(ord("a") + i) * 40), f"feat: {i}") for i in range(5))
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner()
    worker = WorkerConfig(max_commits_per_run=2)
    config = _config(repos=(_repo_cfg(),), worker=worker)

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert len(runner.calls) == 2
    assert summary.events_processed == 2


def test_run_dry_run_history_rewritten_prints_message_no_state_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache()
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("project-a"),))
    out = io.StringIO()

    def fake_iter(
        *,
        cache: object,
        repo_name: str,
        branch: str,
        since_sha: str | None,
        skip_patterns: tuple[str, ...],
        noise_patterns: tuple[str, ...],
        limit: int | None = None,
    ) -> Iterator[IngestEvent]:
        raise HistoryRewrittenError("rewritten")
        yield  # pragma: no cover - generator marker

    monkeypatch.setattr("cadence_memory.worker.run.iter_pending_commits", fake_iter)

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        dry_run=True,
        clock=_fixed_clock(),
        out=out,
    )

    assert "project-a: history rewritten — needs reset" in out.getvalue()
    assert not (wiki / ".cadence-memory" / "state.json").exists()


def test_run_records_failure_on_cache_ensure_error(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(
        ensure_errors={"a": GitError("clone failed: network unreachable")},
        commits_by_repo={"b": (_commit("b" * 40, "feat: b"),)},
    )
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"), _repo_cfg("b")))

    state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert state.repos["a"].last_failure == "clone failed: network unreachable"
    assert state.repos["a"].last_sha is None
    assert summary.repos == ("b",)
    assert summary.events_processed == 1
    on_disk = json.loads((wiki / ".cadence-memory" / "state.json").read_text(encoding="utf-8"))
    assert on_disk["repos"]["a"]["last_failure"] == "clone failed: network unreachable"


def test_run_records_failure_on_iter_pending_git_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(commits_by_repo={"b": (_commit("b" * 40, "feat: b"),)})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"), _repo_cfg("b")))

    def fake_iter(
        *,
        cache: object,
        repo_name: str,
        branch: str,
        since_sha: str | None,
        skip_patterns: tuple[str, ...],
        noise_patterns: tuple[str, ...],
        limit: int | None = None,
    ) -> Iterator[IngestEvent]:
        if repo_name == "a":
            raise GitError("git log failed: bad object")
        yield SingleCommitEvent(repo_name="b", commit=_commit("b" * 40, "feat: b"))

    monkeypatch.setattr("cadence_memory.worker.run.iter_pending_commits", fake_iter)

    state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert state.repos["a"].last_failure == "git log failed: bad object"
    assert state.repos["a"].last_sha is None
    assert summary.repos == ("b",)
    assert summary.events_processed == 1
    on_disk = json.loads((wiki / ".cadence-memory" / "state.json").read_text(encoding="utf-8"))
    assert on_disk["repos"]["a"]["last_failure"] == "git log failed: bad object"


def test_run_dry_run_iter_pending_git_error_prints_message_no_state_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache()
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"),))
    out = io.StringIO()

    def fake_iter(
        *,
        cache: object,
        repo_name: str,
        branch: str,
        since_sha: str | None,
        skip_patterns: tuple[str, ...],
        noise_patterns: tuple[str, ...],
        limit: int | None = None,
    ) -> Iterator[IngestEvent]:
        raise GitError("git log failed: bad object")
        yield  # pragma: no cover - unreachable

    monkeypatch.setattr("cadence_memory.worker.run.iter_pending_commits", fake_iter)

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        dry_run=True,
        clock=_fixed_clock(),
        out=out,
    )

    assert "a: cache read failed — git log failed: bad object" in out.getvalue()
    assert not (wiki / ".cadence-memory" / "state.json").exists()


def test_run_dry_run_cache_ensure_error_prints_message_no_state_write(
    tmp_path: Path,
) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(
        ensure_errors={"a": GitError("clone failed: network unreachable")},
    )
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"),))
    out = io.StringIO()

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        dry_run=True,
        clock=_fixed_clock(),
        out=out,
    )

    assert "a: cache unavailable — clone failed: network unreachable" in out.getvalue()
    assert not (wiki / ".cadence-memory" / "state.json").exists()


def test_run_pending_should_stop_between_events_breaks_inner_loop(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    sha_a = "a" * 40
    sha_b = "b" * 40
    commits = (_commit(sha_a, "feat: one"), _commit(sha_b, "feat: two"))
    cache = _FakeGitCache(commits_by_repo={"project-a": commits})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))

    calls = {"n": 0}

    def stop_after_first() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
        should_stop_between_events=stop_after_first,
    )

    assert len(runner.calls) == 1
    assert summary.events_processed == 1
    repo_state = state.repos["project-a"]
    assert repo_state.last_sha == sha_a
    assert repo_state.commits_processed == 1


def test_run_pending_should_stop_between_repos_breaks_outer_loop(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(
        commits_by_repo={
            "a": (_commit("a" * 40, "feat: a"),),
            "b": (_commit("b" * 40, "feat: b"),),
        }
    )
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"), _repo_cfg("b")))

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
        should_stop_between_repos=lambda: True,
    )

    assert runner.calls == []
    assert summary.repos == ()
    assert summary.events_processed == 0


def test_run_pending_should_stop_between_repos_stops_after_first(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(
        commits_by_repo={
            "a": (_commit("a" * 40, "feat: a"),),
            "b": (_commit("b" * 40, "feat: b"),),
        }
    )
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg("a"), _repo_cfg("b")))

    calls = {"n": 0}

    def stop_after_first() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
        should_stop_between_repos=stop_after_first,
    )

    assert len(runner.calls) == 1
    assert summary.repos == ("a",)
    assert summary.events_processed == 1


def test_run_returns_runsummary_dataclass(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache()
    runner = _FakeClaudeRunner()
    config = _config(repos=())

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert isinstance(summary, RunSummary)
    assert summary.repos == ()
    assert summary.events_processed == 0
    assert summary.events_failed == 0
    assert summary.cost_usd_total == 0.0
