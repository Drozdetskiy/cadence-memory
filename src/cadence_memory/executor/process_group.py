"""POSIX process-group cleanup for the streaming claude executor (design2 §6.1).

Callers MUST spawn the Popen with start_new_session=True so the child becomes
its own process-group leader; this class only reads the pgid of the live
process and signals that group on exit.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from types import TracebackType
from typing import ClassVar


class ProcessGroupCleanup:
    GRACE_S: ClassVar[float] = 3.0
    _POLL_INTERVAL_S: ClassVar[float] = 0.05

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc
        self._pgid: int | None
        if os.name != "nt":
            try:
                self._pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                self._pgid = None
        else:
            self._pgid = None

    def __enter__(self) -> ProcessGroupCleanup:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._proc.poll() is not None:
            return

        if os.name == "nt":
            self._proc.terminate()
            try:
                self._proc.wait(timeout=self.GRACE_S)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            return

        if self._pgid is None:
            return

        try:
            os.killpg(self._pgid, signal.SIGTERM)
        except ProcessLookupError, PermissionError:
            return

        deadline = time.monotonic() + self.GRACE_S
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return
            time.sleep(self._POLL_INTERVAL_S)

        try:
            os.killpg(self._pgid, signal.SIGKILL)
        except ProcessLookupError, PermissionError:
            return
        self._proc.wait()
