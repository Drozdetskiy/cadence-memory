"""Search backends and result types for `cadence-memory query`."""

from cadence_memory.search.backend import (
    NoBackendAvailableError,
    QmdBackend,
    RipgrepBackend,
    SearchBackend,
    SearchError,
    pick_backend,
)
from cadence_memory.search.result import Hit

__all__ = [
    "Hit",
    "NoBackendAvailableError",
    "QmdBackend",
    "RipgrepBackend",
    "SearchBackend",
    "SearchError",
    "pick_backend",
]
