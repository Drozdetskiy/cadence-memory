"""Dry-run reindex variant powering the `status` command."""

from __future__ import annotations

from pathlib import Path

from cadence_memory.config import AnnotationsConfig, Config
from cadence_memory.reindex.engine import ReindexResult, _classify_entries, _utc_now
from cadence_memory.store.interface import Store

__all__ = ["ReindexResult", "diff"]


def diff(
    *,
    config: Config,
    annotations: AnnotationsConfig,
    store: Store,
    store_dir: Path,
) -> ReindexResult:
    _planned, result = _classify_entries(
        config=config,
        annotations=annotations,
        store=store,
        store_dir=store_dir,
        now=_utc_now,
    )
    return result
