"""Tests for the worker lock (design2 §6.1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cadence_memory.worker.lock import WorkerBusyError, worker_lock


def test_worker_lock_yields_normally(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    with worker_lock(lock_path):
        pass
    assert lock_path.exists()


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
    assert lock_path.exists()
