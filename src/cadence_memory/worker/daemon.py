"""Long-running worker daemon: signal handling and interruptible sleep (design2 §11)."""

from __future__ import annotations

import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType

from cadence_memory.config.schema import Config
from cadence_memory.executor.runner import ClaudeRunner
from cadence_memory.git.cache import GitCache
from cadence_memory.worker.lock import WorkerBusyError, worker_lock
from cadence_memory.worker.run import run_pending
from cadence_memory.worker.state import load_state, save_state

__all__ = [
    "EXIT_CLEAN",
    "EXIT_SIGINT",
    "DaemonSignals",
    "run_daemon",
]

EXIT_CLEAN = 0
EXIT_SIGINT = 130


@dataclass(frozen=True, slots=True)
class DaemonSignals:
    terminate: threading.Event = field(default_factory=threading.Event)
    interrupt: threading.Event = field(default_factory=threading.Event)

    def install(self) -> None:
        def _on_terminate(_signum: int, _frame: FrameType | None) -> None:
            self.terminate.set()

        def _on_interrupt(_signum: int, _frame: FrameType | None) -> None:
            self.interrupt.set()

        signal.signal(signal.SIGTERM, _on_terminate)
        signal.signal(signal.SIGINT, _on_interrupt)

    def should_stop_between_repos(self) -> bool:
        return self.terminate.is_set() or self.interrupt.is_set()

    def should_stop_between_events(self) -> bool:
        return self.interrupt.is_set()


def _sleep_interruptible(
    sleep: Callable[[float], None],
    total_s: float,
    signals: DaemonSignals,
) -> None:
    remaining = total_s
    while remaining > 0:
        if signals.terminate.is_set() or signals.interrupt.is_set():
            return
        chunk = 1.0 if remaining > 1.0 else remaining
        sleep(chunk)
        remaining -= chunk


def _default_log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run_daemon(
    *,
    wiki_dir: Path,
    poll_interval_s: int,
    config_factory: Callable[[], Config],
    state_path: Path,
    cache_factory: Callable[[], GitCache],
    runner_factory: Callable[[], ClaudeRunner],
    signals: DaemonSignals | None = None,
    sleep: Callable[[float], None] = time.sleep,
    iterations: int | None = None,
    log: Callable[[str], None] = _default_log,
) -> None:
    if signals is None:
        signals = DaemonSignals()
    signals.install()

    lock_path = wiki_dir / ".cadence-memory" / "worker.lock"
    iteration_count = 0

    while True:
        if signals.terminate.is_set() or signals.interrupt.is_set():
            break

        try:
            with worker_lock(lock_path):
                config = config_factory()
                state = load_state(state_path)
                cache = cache_factory()
                runner = runner_factory()
                new_state, summary = run_pending(
                    wiki_dir=wiki_dir,
                    config=config,
                    state=state,
                    cache=cache,
                    runner=runner,
                    should_stop_between_repos=signals.should_stop_between_repos,
                    should_stop_between_events=signals.should_stop_between_events,
                )
                save_state(state_path, new_state)
                log(
                    f"daemon: processed {summary.events_processed}, "
                    f"failed {summary.events_failed}, "
                    f"${summary.cost_usd_total:.2f}"
                )
        except WorkerBusyError:
            log("daemon: another worker is running; skipping this tick")
        except Exception as exc:
            log(f"daemon: unexpected error: {exc!r}")

        iteration_count += 1
        if iterations is not None and iteration_count >= iterations:
            break
        if signals.terminate.is_set() or signals.interrupt.is_set():
            break

        _sleep_interruptible(sleep, float(poll_interval_s), signals)
