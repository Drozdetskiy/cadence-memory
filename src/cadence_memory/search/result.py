"""Result types for search backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Hit:
    path: Path
    score: float | None
    snippet: str
    backend: Literal["qmd", "ripgrep"]


__all__ = ["Hit"]
