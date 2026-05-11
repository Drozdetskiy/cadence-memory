"""Errors raised by the config loader."""

from __future__ import annotations

from pathlib import Path


class ConfigError(Exception):
    """Raised when a config file fails to load or validate.

    Carries the offending file path so callers can surface a precise location.
    """

    __slots__ = ("message", "path")

    path: Path
    message: str

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message
