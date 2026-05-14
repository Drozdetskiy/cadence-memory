"""Integration tests for progress event emission and logging (design2 §2)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cadence_memory.config.schema import Config, RepoConfig
from cadence_memory.executor.runner import ClaudeResult
from cadence_memory.git.cache import CloneResult, CommitInfo
from cadence_memory.progress.logger import StdoutLogger
from cadence_memory.worker.bootstrap import run_bootstrap
from cadence_memory.worker.run import run_pending
from cadence_memory.worker.state import WorkerState

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_HEAD_SHA = "a" * 40

_STAGE_NAMES: dict[int, str] = {
    1: "data-model",
    2: "routes",
    3: "architecture",
    4: "gaps",
    5: "plans",
}


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


def _init_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git("init", "--initial-branch=main", cwd=wiki)
    _git("config", "user.email", "test@example.com", cwd=wiki)
    _git("config", "user.name", "Test User", cwd=wiki)
    (wiki / "index.md").write_text(
        '---\ntitle: "Index"\ntype: overview\nproject: _master\n'
        "created: 2026-05-12\nupdated: 2026-05-12\ntags: []\nconfidence: high\n---\n\n"
        "**TLDR**: catalog.\n",
        encoding="utf-8",
    )
    (wiki / "log.md").write_text(
        '---\ntitle: "Activity Log"\ntype: log\nproject: _master\n'
        "created: 2026-05-12\nupdated: 2026-05-12\ntags: []\nconfidence: high\n---\n\n"
        "## [2026-05-12] init | wiki scaffolded\n\nseed entry.\n",
        encoding="utf-8",
    )
    _git("add", "-A", cwd=wiki)
    _git("commit", "-m", "seed", cwd=wiki)
    return wiki


@dataclass
class _FakeBootstrapCache:
    """Minimal GitCache for bootstrap: only `ensure()` is called."""

    repo_path: Path
    head_sha: str = _HEAD_SHA

    def ensure(self, *, name: str, url: str, branch: str) -> CloneResult:
        return CloneResult(path=self.repo_path, head_sha=self.head_sha, was_initial_clone=False)

    def head(self, *, name: str, branch: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:  # pragma: no cover
        raise NotImplementedError

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:  # pragma: no cover
        raise NotImplementedError

    def diff(self, *, name: str, sha: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:  # pragma: no cover
        raise NotImplementedError

    def list_commits(
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]:  # pragma: no cover
        raise NotImplementedError


@dataclass
class _FakeDryRunCache:
    """GitCache for dry-run: `ensure()` succeeds and `list_commits` returns fixed commits."""

    commits: tuple[CommitInfo, ...] = ()
    _repo_path: Path = field(default_factory=lambda: Path("/tmp/fake"))

    def ensure(self, *, name: str, url: str, branch: str) -> CloneResult:
        return CloneResult(path=self._repo_path, head_sha=_HEAD_SHA, was_initial_clone=False)

    def head(self, *, name: str, branch: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:  # pragma: no cover
        raise NotImplementedError

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:  # pragma: no cover
        raise NotImplementedError

    def diff(self, *, name: str, sha: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def changed_files(self, *, name: str, sha: str) -> tuple[str, ...]:  # pragma: no cover
        raise NotImplementedError

    def list_commits(
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]:
        return self.commits


@dataclass
class _FakeRunner:
    """Always returns a successful ClaudeResult."""

    calls: list[dict[str, object]] = field(default_factory=list)

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
        self.calls.append({"model": model})
        return ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.01,
            duration_ms=50,
            tool_call_count=0,
            error=None,
        )


def _config(repos: tuple[RepoConfig, ...] = ()) -> Config:
    return Config(model="claude-sonnet-4-6", idle_timeout_s=300, repos=repos)


def _repo_cfg(name: str = "proj") -> RepoConfig:
    return RepoConfig(name=name, url="https://example.com/proj.git", branch="main")


def test_bootstrap_stage_section_dividers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """run_bootstrap emits one `=== bootstrap stage N/5: <name> ===` header per stage."""
    wiki = _init_wiki(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    logger = StdoutLogger(level="info", color="never")

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=_FakeBootstrapCache(repo_path=repo_path),
        runner=_FakeRunner(),
        logger=logger,
    )

    captured = capsys.readouterr()
    for stage, name in _STAGE_NAMES.items():
        header = f"=== bootstrap stage {stage}/5: {name} ==="
        assert header in captured.out, f"Missing stage section divider: {header!r}"
        assert f"stage {stage} done" in captured.out, (
            f"Missing stage-end summary line for stage {stage}"
        )


def test_jsonl_sink_produces_valid_records(tmp_path: Path) -> None:
    """StdoutLogger with jsonl_path writes one valid JSON record per log_event call."""
    wiki = _init_wiki(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    jsonl_path = str(tmp_path / "events.jsonl")
    logger = StdoutLogger(level="info", color="never", jsonl_path=jsonl_path)

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=_FakeBootstrapCache(repo_path=repo_path),
        runner=_FakeRunner(),
        logger=logger,
    )

    lines = Path(jsonl_path).read_text(encoding="utf-8").splitlines()
    # PhaseStart + 5 * (StageStart + StageEnd) + PhaseEnd = 12 records
    assert len(lines) == 12, f"Expected 12 JSONL records, got {len(lines)}"
    for line in lines:
        obj = json.loads(line)
        assert "event" in obj, f"Missing 'event' field in: {line}"
        assert "ts" in obj, f"Missing 'ts' field in: {line}"


def test_non_tty_stdout_has_no_ansi(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """StdoutLogger with color='auto' emits no ANSI codes when stdout is not a tty."""
    wiki = _init_wiki(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    # capsys redirects sys.stdout to a StringIO which is not a tty → auto = never
    logger = StdoutLogger(level="info", color="auto")

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=_FakeBootstrapCache(repo_path=repo_path),
        runner=_FakeRunner(),
        logger=logger,
    )

    captured = capsys.readouterr()
    assert _ANSI_RE.search(captured.out) is None, "ANSI codes found in non-tty stdout"


def test_log_md_unchanged_after_successful_bootstrap(tmp_path: Path) -> None:
    """Successful bootstrap does not modify log.md — the stdout sink is purely additive."""
    wiki = _init_wiki(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    log_before = (wiki / "log.md").read_bytes()
    logger = StdoutLogger(level="info", color="never")

    run_bootstrap(
        repo_cfg=_repo_cfg(),
        config=_config(),
        wiki_dir=wiki,
        cache=_FakeBootstrapCache(repo_path=repo_path),
        runner=_FakeRunner(),
        logger=logger,
    )

    log_after = (wiki / "log.md").read_bytes()
    assert log_after == log_before, "log.md was modified during the bootstrap run"


def _make_commit(sha: str, subject: str = "feat: change") -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject=subject,
        body="",
        author_date_iso="2026-05-12T12:00:00+00:00",
        parents=(),
    )


def test_dry_run_no_claude_progress_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """worker run --dry-run never invokes the runner and emits no ClaudeProgressEvent lines."""
    wiki = _init_wiki(tmp_path)
    commits = (_make_commit("a" * 40, "feat: add feature"),)
    cache = _FakeDryRunCache(commits=commits)
    runner = _FakeRunner()
    config = _config(repos=(_repo_cfg(),))
    logger = StdoutLogger(level="debug", color="never")

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        dry_run=True,
        logger=logger,
    )

    assert runner.calls == [], "Runner was invoked during dry-run"

    captured = capsys.readouterr()
    assert "plan:" in captured.out
    assert "feat: add feature" in captured.out

    for line in captured.out.splitlines():
        assert "] tool-call" not in line, f"ClaudeProgressEvent tool-call in dry-run output: {line}"
        assert "] signal" not in line, f"ClaudeProgressEvent signal in dry-run output: {line}"
        assert "] tool-result-error" not in line, (
            f"ClaudeProgressEvent tool-result-error in dry-run output: {line}"
        )
