"""Tests for ProcessGroupCleanup."""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from cadence_memory.executor.process_group import ProcessGroupCleanup

skip_on_windows = pytest.mark.skipif(os.name == "nt", reason="POSIX-only")


def _wait_dead(proc: subprocess.Popen[bytes], deadline_s: float) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if proc.poll() is not None:
            return True
        time.sleep(0.02)
    return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@skip_on_windows
def test_process_group_kills_subprocess() -> None:
    proc = subprocess.Popen(
        ["sleep", "60"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    with ProcessGroupCleanup(proc):
        pass
    assert _wait_dead(proc, ProcessGroupCleanup.GRACE_S + 0.5)
    assert proc.returncode is not None
    assert proc.returncode != 0


@skip_on_windows
def test_process_group_no_op_when_already_exited() -> None:
    proc = subprocess.Popen(
        ["true"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    proc.wait()
    with ProcessGroupCleanup(proc):
        pass
    assert proc.returncode == 0


@skip_on_windows
def test_process_group_kills_child_grandchildren() -> None:
    proc = subprocess.Popen(
        [
            "sh",
            "-c",
            'sleep 60 & echo "$!"; sleep 60 & echo "$!"; wait',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None
    line1 = proc.stdout.readline().strip()
    line2 = proc.stdout.readline().strip()
    grandchild_pids = [int(line1), int(line2)]
    for pid in grandchild_pids:
        assert _pid_alive(pid)

    with ProcessGroupCleanup(proc):
        pass

    assert _wait_dead(proc, ProcessGroupCleanup.GRACE_S + 0.5)
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if not any(_pid_alive(pid) for pid in grandchild_pids):
            break
        time.sleep(0.02)
    for pid in grandchild_pids:
        assert not _pid_alive(pid), f"grandchild {pid} still alive"


@skip_on_windows
def test_process_group_swallows_permission_error_on_killpg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = subprocess.Popen(
        ["sleep", "60"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        cleanup = ProcessGroupCleanup(proc)

        def raise_permission_error(pgid: int, sig: int) -> None:
            raise PermissionError("simulated pgid recycled to other-user group")

        monkeypatch.setattr(os, "killpg", raise_permission_error)

        with cleanup:
            pass
    finally:
        monkeypatch.undo()
        proc.terminate()
        proc.wait()


@skip_on_windows
def test_process_group_graceful_then_force() -> None:
    proc = subprocess.Popen(
        [
            "python3",
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    time.sleep(0.2)
    start = time.monotonic()
    with ProcessGroupCleanup(proc):
        pass
    elapsed = time.monotonic() - start

    assert _wait_dead(proc, 0.5)
    assert elapsed >= ProcessGroupCleanup.GRACE_S - 0.1
    assert elapsed < ProcessGroupCleanup.GRACE_S + 1.0
    assert proc.returncode is not None
    assert proc.returncode != 0
