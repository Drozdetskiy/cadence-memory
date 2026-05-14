"""CLI-layer helpers shared between cli.py and cli_commands/*.py (design2 §2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import typer

from cadence_memory.config.schema import Config
from cadence_memory.progress.logger import ColorMode, Level, Logger, StdoutLogger


@dataclass
class CliOverrides:
    verbose: bool = False
    quiet: bool = False
    no_color: bool = False


def get_overrides(ctx: typer.Context) -> CliOverrides:
    obj = ctx.obj
    if isinstance(obj, CliOverrides):
        return obj
    return CliOverrides()


def make_logger(
    cfg: Config | None,
    overrides: CliOverrides | None = None,
    wiki_dir: Path | None = None,
) -> Logger:
    o = overrides or CliOverrides()
    level: Level
    if o.verbose:
        level = "debug"
    elif o.quiet:
        level = "warn"
    elif cfg is not None:
        level = cast(Level, cfg.progress.level)
    else:
        level = "info"

    color: ColorMode
    if o.no_color:
        color = "never"
    elif cfg is not None:
        color = cast(ColorMode, cfg.progress.color)
    else:
        color = "auto"

    jsonl_path: str | None = None
    if cfg is not None and cfg.progress.jsonl:
        raw = Path(cfg.progress.jsonl_path)
        if not raw.is_absolute() and wiki_dir is not None:
            jsonl_path = str(wiki_dir / raw)
        else:
            jsonl_path = str(raw)

    return StdoutLogger(level=level, color=color, jsonl_path=jsonl_path)


__all__ = ["CliOverrides", "get_overrides", "make_logger"]
