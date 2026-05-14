"""Non-blocking POSIX flock for the worker (design2 §6.1).

POSIX uses `fcntl.flock(LOCK_EX | LOCK_NB)`; Windows has no equivalent in
stdlib so we ship a no-op shim there until a Windows user files an issue.
The lock file is removed when the worker exits (normal or exception path),
so the file's presence is not a reliable "is busy?" signal — only the
kernel-side flock is.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["WorkerBusyError", "worker_lock"]


class WorkerBusyError(Exception):
    """Raised when another worker already holds the lock."""


if sys.platform == "win32":

    @contextmanager
    def worker_lock(path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        yield

else:
    import fcntl

    @contextmanager
    def worker_lock(path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkerBusyError(f"another worker holds {path}") from exc
            try:
                disk_ino = os.stat(path).st_ino
            except FileNotFoundError as exc:
                raise WorkerBusyError(f"another worker holds {path}") from exc
            if disk_ino != os.fstat(f.fileno()).st_ino:
                raise WorkerBusyError(f"another worker holds {path}")
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
