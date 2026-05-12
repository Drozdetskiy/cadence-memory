"""Tests for the worker daemon signal primitives (design2 §11)."""

from __future__ import annotations

import json
import signal
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from cadence_memory.config.schema import Config
from cadence_memory.worker.daemon import (
    EXIT_CLEAN,
    EXIT_SIGINT,
    DaemonSignals,
    _sleep_interruptible,
    run_daemon,
)
from cadence_memory.worker.lock import WorkerBusyError
from cadence_memory.worker.run import RunSummary
from cadence_memory.worker.state import RepoState, WorkerState


def test_exit_code_constants() -> None:
    assert EXIT_CLEAN == 0
    assert EXIT_SIGINT == 130


def test_signals_install_replaces_handlers() -> None:
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    signals = DaemonSignals()
    try:
        signals.install()
        new_term = signal.getsignal(signal.SIGTERM)
        new_int = signal.getsignal(signal.SIGINT)
        assert new_term is not previous_term
        assert new_int is not previous_int
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def test_signals_predicates() -> None:
    signals = DaemonSignals()
    assert signals.should_stop_between_repos() is False
    assert signals.should_stop_between_events() is False

    signals.terminate.set()
    assert signals.should_stop_between_repos() is True
    assert signals.should_stop_between_events() is False

    signals.terminate.clear()
    signals.interrupt.set()
    assert signals.should_stop_between_repos() is True
    assert signals.should_stop_between_events() is True


def test_sleep_interruptible_completes_when_no_signal() -> None:
    calls: list[float] = []
    signals = DaemonSignals()
    _sleep_interruptible(calls.append, 3.0, signals)
    assert calls == [1.0, 1.0, 1.0]


def test_sleep_interruptible_breaks_on_terminate() -> None:
    calls: list[float] = []
    signals = DaemonSignals()

    def fake_sleep(chunk: float) -> None:
        calls.append(chunk)
        if len(calls) == 2:
            signals.terminate.set()

    _sleep_interruptible(fake_sleep, 10.0, signals)
    assert calls == [1.0, 1.0]


def test_sleep_interruptible_breaks_on_interrupt() -> None:
    calls: list[float] = []
    signals = DaemonSignals()

    def fake_sleep(chunk: float) -> None:
        calls.append(chunk)
        if len(calls) == 2:
            signals.interrupt.set()

    _sleep_interruptible(fake_sleep, 10.0, signals)
    assert calls == [1.0, 1.0]


def test_sleep_interruptible_zero_total_does_not_sleep() -> None:
    calls: list[float] = []
    signals = DaemonSignals()
    _sleep_interruptible(calls.append, 0.0, signals)
    assert calls == []


def test_sleep_interruptible_signal_already_set() -> None:
    calls: list[float] = []
    signals = DaemonSignals()
    signals.terminate.set()
    _sleep_interruptible(calls.append, 3.0, signals)
    assert calls == []


@pytest.mark.parametrize("total", [0.5, 0.25])
def test_sleep_interruptible_handles_sub_second_total(total: float) -> None:
    calls: list[float] = []
    signals = DaemonSignals()
    _sleep_interruptible(calls.append, total, signals)
    assert calls == [total]


@dataclass
class _FakeRunCall:
    config: Config
    state: WorkerState
    kwargs: dict[str, Any]


@dataclass
class _FakeRunPending:
    side_effects: list[Callable[[_FakeRunCall], tuple[WorkerState, RunSummary]]] = field(
        default_factory=list
    )
    calls: list[_FakeRunCall] = field(default_factory=list)
    default: tuple[WorkerState, RunSummary] = field(
        default_factory=lambda: (
            WorkerState(),
            RunSummary(repos=(), events_processed=0, events_failed=0, cost_usd_total=0.0),
        )
    )

    def __call__(
        self,
        *,
        wiki_dir: Path,
        config: Config,
        state: WorkerState,
        cache: object,
        runner: object,
        **kwargs: Any,
    ) -> tuple[WorkerState, RunSummary]:
        call = _FakeRunCall(config=config, state=state, kwargs=kwargs)
        self.calls.append(call)
        if self.side_effects:
            return self.side_effects.pop(0)(call)
        return self.default


def _scaffold_wiki(tmp_path: Path) -> tuple[Path, Path]:
    wiki = tmp_path / "wiki"
    (wiki / ".cadence-memory").mkdir(parents=True)
    state_path = wiki / ".cadence-memory" / "state.json"
    return wiki, state_path


def _empty_config() -> Config:
    return Config()


def _restoring_signals() -> tuple[Callable[[], None], DaemonSignals]:
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)

    def restore() -> None:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)

    return restore, DaemonSignals()


def test_daemon_runs_one_iteration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki, state_path = _scaffold_wiki(tmp_path)
    fake = _FakeRunPending(
        default=(
            WorkerState(repos={"project-a": RepoState(last_sha="a" * 40, commits_processed=1)}),
            RunSummary(
                repos=("project-a",),
                events_processed=1,
                events_failed=0,
                cost_usd_total=0.02,
            ),
        )
    )
    monkeypatch.setattr("cadence_memory.worker.daemon.run_pending", fake)
    sleep_calls: list[float] = []
    restore, signals = _restoring_signals()
    try:
        run_daemon(
            wiki_dir=wiki,
            poll_interval_s=10,
            config_factory=_empty_config,
            state_path=state_path,
            cache_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            runner_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            signals=signals,
            sleep=sleep_calls.append,
            iterations=1,
            log=lambda _msg: None,
        )
    finally:
        restore()

    assert len(fake.calls) == 1
    assert state_path.exists()
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert on_disk["repos"]["project-a"]["last_sha"] == "a" * 40
    assert sleep_calls == []


def test_daemon_respects_terminate_between_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, state_path = _scaffold_wiki(tmp_path)
    restore, signals = _restoring_signals()

    def terminating_run(_call: _FakeRunCall) -> tuple[WorkerState, RunSummary]:
        signals.terminate.set()
        return (
            WorkerState(),
            RunSummary(repos=(), events_processed=0, events_failed=0, cost_usd_total=0.0),
        )

    fake = _FakeRunPending(side_effects=[terminating_run])
    monkeypatch.setattr("cadence_memory.worker.daemon.run_pending", fake)
    sleep_calls: list[float] = []
    try:
        run_daemon(
            wiki_dir=wiki,
            poll_interval_s=10,
            config_factory=_empty_config,
            state_path=state_path,
            cache_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            runner_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            signals=signals,
            sleep=sleep_calls.append,
            iterations=None,
            log=lambda _msg: None,
        )
    finally:
        restore()

    assert len(fake.calls) == 1
    assert sleep_calls == []


def test_daemon_respects_interrupt_between_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, state_path = _scaffold_wiki(tmp_path)
    restore, signals = _restoring_signals()

    def interrupting_run(_call: _FakeRunCall) -> tuple[WorkerState, RunSummary]:
        signals.interrupt.set()
        return (
            WorkerState(),
            RunSummary(repos=(), events_processed=0, events_failed=0, cost_usd_total=0.0),
        )

    fake = _FakeRunPending(side_effects=[interrupting_run])
    monkeypatch.setattr("cadence_memory.worker.daemon.run_pending", fake)
    sleep_calls: list[float] = []
    try:
        run_daemon(
            wiki_dir=wiki,
            poll_interval_s=10,
            config_factory=_empty_config,
            state_path=state_path,
            cache_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            runner_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            signals=signals,
            sleep=sleep_calls.append,
            iterations=None,
            log=lambda _msg: None,
        )
    finally:
        restore()

    assert len(fake.calls) == 1
    assert sleep_calls == []


def test_daemon_passes_should_stop_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, state_path = _scaffold_wiki(tmp_path)
    fake = _FakeRunPending()
    monkeypatch.setattr("cadence_memory.worker.daemon.run_pending", fake)
    restore, signals = _restoring_signals()
    try:
        run_daemon(
            wiki_dir=wiki,
            poll_interval_s=10,
            config_factory=_empty_config,
            state_path=state_path,
            cache_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            runner_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            signals=signals,
            sleep=lambda _s: None,
            iterations=1,
            log=lambda _msg: None,
        )
    finally:
        restore()

    assert len(fake.calls) == 1
    kwargs = fake.calls[0].kwargs
    stop_repos = kwargs["should_stop_between_repos"]
    stop_events = kwargs["should_stop_between_events"]
    assert callable(stop_repos)
    assert callable(stop_events)
    assert stop_repos() is False
    assert stop_events() is False
    signals.interrupt.set()
    assert stop_repos() is True
    assert stop_events() is True


def test_daemon_rereads_config_each_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, state_path = _scaffold_wiki(tmp_path)
    fake = _FakeRunPending()
    monkeypatch.setattr("cadence_memory.worker.daemon.run_pending", fake)
    factory_calls = {"n": 0}

    def config_factory() -> Config:
        factory_calls["n"] += 1
        return Config()

    restore, signals = _restoring_signals()
    try:
        run_daemon(
            wiki_dir=wiki,
            poll_interval_s=0,
            config_factory=config_factory,
            state_path=state_path,
            cache_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            runner_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            signals=signals,
            sleep=lambda _s: None,
            iterations=2,
            log=lambda _msg: None,
        )
    finally:
        restore()

    assert factory_calls["n"] == 2
    assert len(fake.calls) == 2


def test_daemon_swallows_unexpected_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, state_path = _scaffold_wiki(tmp_path)

    def boom(_call: _FakeRunCall) -> tuple[WorkerState, RunSummary]:
        raise RuntimeError("boom")

    def ok(_call: _FakeRunCall) -> tuple[WorkerState, RunSummary]:
        return (
            WorkerState(),
            RunSummary(repos=(), events_processed=0, events_failed=0, cost_usd_total=0.0),
        )

    fake = _FakeRunPending(side_effects=[boom, ok])
    monkeypatch.setattr("cadence_memory.worker.daemon.run_pending", fake)
    logs: list[str] = []
    restore, signals = _restoring_signals()
    try:
        run_daemon(
            wiki_dir=wiki,
            poll_interval_s=0,
            config_factory=_empty_config,
            state_path=state_path,
            cache_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            runner_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            signals=signals,
            sleep=lambda _s: None,
            iterations=2,
            log=logs.append,
        )
    finally:
        restore()

    assert len(fake.calls) == 2
    assert any("unexpected error" in line for line in logs)


def test_daemon_preserves_state_when_run_pending_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, state_path = _scaffold_wiki(tmp_path)
    sentinel = {
        "schema_version": 1,
        "repos": {"sentinel": {"last_sha": "s" * 40, "commits_processed": 7}},
    }
    state_path.write_text(json.dumps(sentinel), encoding="utf-8")

    def boom(_call: _FakeRunCall) -> tuple[WorkerState, RunSummary]:
        raise RuntimeError("boom")

    fake = _FakeRunPending(side_effects=[boom])
    monkeypatch.setattr("cadence_memory.worker.daemon.run_pending", fake)
    restore, signals = _restoring_signals()
    try:
        run_daemon(
            wiki_dir=wiki,
            poll_interval_s=0,
            config_factory=_empty_config,
            state_path=state_path,
            cache_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            runner_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            signals=signals,
            sleep=lambda _s: None,
            iterations=1,
            log=lambda _msg: None,
        )
    finally:
        restore()

    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert on_disk["repos"]["sentinel"]["last_sha"] == "s" * 40
    assert on_disk["repos"]["sentinel"]["commits_processed"] == 7


def test_daemon_swallows_worker_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki, state_path = _scaffold_wiki(tmp_path)
    fake = _FakeRunPending()
    monkeypatch.setattr("cadence_memory.worker.daemon.run_pending", fake)

    lock_calls = {"n": 0}

    @contextmanager
    def fake_lock(_path: Path):  # type: ignore[no-untyped-def]
        lock_calls["n"] += 1
        if lock_calls["n"] == 1:
            raise WorkerBusyError("busy")
        yield

    monkeypatch.setattr("cadence_memory.worker.daemon.worker_lock", fake_lock)
    logs: list[str] = []
    restore, signals = _restoring_signals()
    try:
        run_daemon(
            wiki_dir=wiki,
            poll_interval_s=0,
            config_factory=_empty_config,
            state_path=state_path,
            cache_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            runner_factory=lambda: object(),  # type: ignore[arg-type, return-value]
            signals=signals,
            sleep=lambda _s: None,
            iterations=2,
            log=logs.append,
        )
    finally:
        restore()

    assert len(fake.calls) == 1
    assert any("skipping this tick" in line for line in logs)
