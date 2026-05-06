"""Default `exclude` globs for Python projects.

Exposed as a public module so `cadence-memory init` and the upcoming
`projects add` / autodetect work in task 0021 share a single source of
truth.
"""

from __future__ import annotations

from typing import Final

DEFAULT_PROJECT_EXCLUDE: Final[tuple[str, ...]] = (
    # Python virtual envs / installed packages
    "**/.venv/**",
    "**/venv/**",
    "**/env/**",
    "**/.pip_packages/**",
    "**/site-packages/**",
    # Python caches
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/.tox/**",
    "**/.nox/**",
    # Coverage / test artefacts
    "**/htmlcov/**",
    "**/.coverage",
    "**/coverage.xml",
    # Build artefacts
    "**/dist/**",
    "**/build/**",
    "**/*.egg-info/**",
    # VCS / generic
    "**/.git/**",
    "**/node_modules/**",
)
