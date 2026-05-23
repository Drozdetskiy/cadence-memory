"""Tests for the dirty-wiki pre-flight in `run_pending` (task 1040)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cadence_memory.progress.events import WikiDirtyPreflightEvent
from cadence_memory.worker.run import WikiDirtyError, run_pending
from cadence_memory.worker.state import WorkerState
from tests.worker.test_run import (
    _commit,
    _config,
    _FakeClaudeRunner,
    _FakeGitCache,
    _fixed_clock,
    _init_wiki,
    _RecordingLogger,
    _repo_cfg,
)


def test_preflight_warns_on_dirty_wiki(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "index.md").write_text("dirty edit\n", encoding="utf-8")
    cache = _FakeGitCache(commits_by_repo={"project-a": (_commit("a" * 40, "feat: x"),)})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))
    recording = _RecordingLogger()

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
        logger=recording,
    )

    preflight = [e for e in recording.events if isinstance(e, WikiDirtyPreflightEvent)]
    assert len(preflight) == 1
    assert preflight[0].proceeded is True
    assert "index.md" in preflight[0].paths
    assert len(runner.calls) == 1
    assert summary.events_processed == 1


def test_preflight_includes_untracked(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "brand-new.md").write_text("untracked\n", encoding="utf-8")
    cache = _FakeGitCache()
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))
    recording = _RecordingLogger()

    run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
        logger=recording,
    )

    preflight = [e for e in recording.events if isinstance(e, WikiDirtyPreflightEvent)]
    assert len(preflight) == 1
    assert "brand-new.md" in preflight[0].paths


def test_preflight_silent_on_clean_wiki(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    cache = _FakeGitCache(commits_by_repo={"project-a": (_commit("a" * 40, "feat: x"),)})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))
    recording = _RecordingLogger()

    _state, summary = run_pending(
        wiki_dir=wiki,
        config=config,
        state=WorkerState(),
        cache=cache,
        runner=runner,
        clock=_fixed_clock(),
        logger=recording,
    )

    preflight = [e for e in recording.events if isinstance(e, WikiDirtyPreflightEvent)]
    assert preflight == []
    assert len(runner.calls) == 1
    assert summary.events_processed == 1


def test_preflight_strict_clean_raises_before_ingest(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "index.md").write_text("dirty edit\n", encoding="utf-8")
    cache = _FakeGitCache(commits_by_repo={"project-a": (_commit("a" * 40, "feat: x"),)})
    runner = _FakeClaudeRunner()
    config = _config(repos=(_repo_cfg(),))
    recording = _RecordingLogger()

    with pytest.raises(WikiDirtyError) as exc_info:
        run_pending(
            wiki_dir=wiki,
            config=config,
            state=WorkerState(),
            cache=cache,
            runner=runner,
            strict_clean=True,
            clock=_fixed_clock(),
            logger=recording,
        )

    assert "index.md" in exc_info.value.paths
    preflight = [e for e in recording.events if isinstance(e, WikiDirtyPreflightEvent)]
    assert len(preflight) == 1
    assert preflight[0].proceeded is False
    assert runner.calls == []
    assert not (wiki / ".cadence-memory" / "state.json").exists()
