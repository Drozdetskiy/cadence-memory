"""Tests for the worker lock (design2 §6.1)."""

from __future__ import annotations

import multiprocessing
import sys
import time
from pathlib import Path

import pytest

from cadence_memory.worker.lock import WorkerBusyError, worker_lock


def test_worker_lock_yields_normally(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    with worker_lock(lock_path):
        pass
    assert not lock_path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
def test_worker_lock_blocks_concurrent_holder(tmp_path: Path) -> None:
    import fcntl

    lock_path = tmp_path / "worker.lock"
    with open(lock_path, "w") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(WorkerBusyError) as exc_info, worker_lock(lock_path):
                pass
            assert str(lock_path) in str(exc_info.value)
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)


def test_worker_lock_released_after_normal_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    with worker_lock(lock_path):
        pass
    with worker_lock(lock_path):
        pass


def test_worker_lock_released_on_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), worker_lock(lock_path):
        raise _Boom

    with worker_lock(lock_path):
        pass


def test_worker_lock_creates_parent_dir(tmp_path: Path) -> None:
    lock_path = tmp_path / "nested" / "deeper" / "x.lock"
    with worker_lock(lock_path):
        pass
    assert lock_path.parent.is_dir()


def test_worker_lock_removes_file_on_clean_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    with worker_lock(lock_path):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_worker_lock_removes_file_on_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), worker_lock(lock_path):
        raise _Boom

    assert not lock_path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
def test_worker_lock_recovers_from_stale_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    lock_path.touch()
    assert lock_path.exists()
    with worker_lock(lock_path):
        pass
    assert not lock_path.exists()


def test_worker_lock_two_sequential_acquires(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    with worker_lock(lock_path):
        pass
    assert not lock_path.exists()
    with worker_lock(lock_path):
        pass
    assert not lock_path.exists()


def _race_child(
    lock_path: Path,
    sentinel_path: Path,
    iterations: int,
    queue: multiprocessing.Queue[tuple[int, int, int, int]],
) -> None:
    import os

    pid = os.getpid()
    successes = 0
    busy = 0
    violations = 0
    while successes < iterations:
        try:
            with worker_lock(lock_path):
                sentinel_path.write_text(str(pid))
                time.sleep(0.005)
                if sentinel_path.read_text() != str(pid):
                    violations += 1
                successes += 1
        except WorkerBusyError:
            busy += 1
            time.sleep(0.001)
    queue.put((pid, successes, busy, violations))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
def test_worker_lock_raises_when_path_unlinked_between_open_and_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from cadence_memory.worker import lock as lock_mod

    lock_path = tmp_path / "worker.lock"
    real_stat = os.stat

    def fake_stat(target: object, *args: object, **kwargs: object) -> os.stat_result:
        if isinstance(target, (str, os.PathLike)) and Path(target) == lock_path:
            raise FileNotFoundError(2, "no such file", str(lock_path))
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(lock_mod.os, "stat", fake_stat)
    with pytest.raises(WorkerBusyError), worker_lock(lock_path):
        pass
    monkeypatch.undo()
    assert lock_path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
def test_worker_lock_raises_on_inode_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from cadence_memory.worker import lock as lock_mod

    lock_path = tmp_path / "worker.lock"
    real_stat = os.stat

    class _FakeStat:
        st_ino = -1

    def fake_stat(target: object, *args: object, **kwargs: object) -> object:
        if isinstance(target, (str, os.PathLike)) and Path(target) == lock_path:
            return _FakeStat()
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(lock_mod.os, "stat", fake_stat)
    with pytest.raises(WorkerBusyError), worker_lock(lock_path):
        pass
    monkeypatch.undo()
    assert lock_path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
def test_worker_lock_concurrent_acquire_single_winner(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    sentinel_path = tmp_path / "sentinel.txt"
    sentinel_path.write_text("init")
    n_children = 8
    iterations = 20
    ctx = multiprocessing.get_context("fork")
    queue: multiprocessing.Queue[tuple[int, int, int, int]] = ctx.Queue()
    procs = [
        ctx.Process(
            target=_race_child,
            args=(lock_path, sentinel_path, iterations, queue),
        )
        for _ in range(n_children)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0, f"child {p.pid} exited with {p.exitcode}"
    results = [queue.get(timeout=5) for _ in range(n_children)]
    total_successes = sum(r[1] for r in results)
    total_violations = sum(r[3] for r in results)
    assert total_successes == n_children * iterations, (
        f"expected exactly {n_children * iterations} successful acquires across all children, "
        f"got {total_successes}; results={results}"
    )
    assert total_violations == 0, (
        f"critical-section overlap detected: {total_violations} violations; results={results}"
    )
