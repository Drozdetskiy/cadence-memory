"""Strict YAML loader for the v2 master-wiki config (design2 §5)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import yaml

from cadence_memory.config.errors import ConfigError
from cadence_memory.config.schema import Config, ProgressConfig, RepoConfig, WorkerConfig

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ALLOWED_TOP_KEYS = frozenset(
    {"model", "budget_usd", "idle_timeout_s", "worker", "repos", "raw_auto_ingest", "progress"}
)
ALLOWED_PROGRESS_KEYS = frozenset({"jsonl", "jsonl_path", "color", "level"})
_VALID_COLORS = frozenset({"auto", "always", "never"})
_VALID_LEVELS = frozenset({"debug", "info", "warn", "error"})
ALLOWED_WORKER_KEYS = frozenset(
    {
        "poll_interval_s",
        "noise_subject_patterns",
        "skip_subject_patterns",
        "max_commits_per_run",
        "stop_on_failure",
    }
)
ALLOWED_REPO_KEYS = frozenset(
    {"name", "url", "branch", "start_commit", "model", "budget_usd", "exclude"}
)


def load_config(path: Path) -> Config:
    """Read and parse a config file. Raises `ConfigError` on any problem."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(path, "file not found") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(path, f"invalid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(path, "top-level must be a mapping")
    data = cast(dict[str, object], raw)
    return parse_config(data, path=path)


def parse_config(data: dict[str, object], *, path: Path) -> Config:
    """Validate an already-parsed mapping and build a `Config`."""
    unknown = set(data) - ALLOWED_TOP_KEYS
    if unknown:
        raise ConfigError(path, f"unknown key(s): {sorted(unknown, key=str)}")

    model = _require_nonempty_str(data.get("model", "claude-sonnet-4-6"), "model", path)
    budget_usd = _parse_optional_budget(data.get("budget_usd", 0.50), "budget_usd", path)
    idle_timeout_s = _require_positive_int(data.get("idle_timeout_s", 300), "idle_timeout_s", path)
    raw_auto_ingest = _require_bool(data.get("raw_auto_ingest", False), "raw_auto_ingest", path)
    worker = _parse_worker(data.get("worker"), path)
    repos = _parse_repos(data.get("repos"), path)
    progress = _parse_progress(data.get("progress"), path)

    return Config(
        model=model,
        budget_usd=budget_usd,
        idle_timeout_s=idle_timeout_s,
        worker=worker,
        repos=repos,
        raw_auto_ingest=raw_auto_ingest,
        progress=progress,
    )


def _parse_progress(value: object, path: Path) -> ProgressConfig:
    if value is None:
        return ProgressConfig()
    if not isinstance(value, dict):
        raise ConfigError(path, "progress: must be a mapping")
    pdata = cast(dict[str, object], value)
    unknown = set(pdata) - ALLOWED_PROGRESS_KEYS
    if unknown:
        raise ConfigError(path, f"progress: unknown key(s): {sorted(unknown, key=str)}")

    jsonl_raw = pdata.get("jsonl", False)
    if not isinstance(jsonl_raw, bool):
        raise ConfigError(path, "progress.jsonl: must be a boolean")

    jsonl_path_raw = pdata.get("jsonl_path", ".cadence-memory/progress.jsonl")
    if not isinstance(jsonl_path_raw, str) or not jsonl_path_raw:
        raise ConfigError(path, "progress.jsonl_path: must be a non-empty string")

    color_raw = pdata.get("color", "auto")
    if not isinstance(color_raw, str) or color_raw not in _VALID_COLORS:
        raise ConfigError(path, f"progress.color: must be one of {sorted(_VALID_COLORS)}")

    level_raw = pdata.get("level", "info")
    if not isinstance(level_raw, str) or level_raw not in _VALID_LEVELS:
        raise ConfigError(path, f"progress.level: must be one of {sorted(_VALID_LEVELS)}")

    return ProgressConfig(
        jsonl=jsonl_raw,
        jsonl_path=jsonl_path_raw,
        color=color_raw,
        level=level_raw,
    )


def _parse_worker(value: object, path: Path) -> WorkerConfig:
    if value is None:
        return WorkerConfig()
    if not isinstance(value, dict):
        raise ConfigError(path, "worker: must be a mapping")
    wdata = cast(dict[str, object], value)
    unknown = set(wdata) - ALLOWED_WORKER_KEYS
    if unknown:
        raise ConfigError(path, f"worker: unknown key(s): {sorted(unknown, key=str)}")

    poll_interval_s = _require_positive_int(
        wdata.get("poll_interval_s", 3600), "worker.poll_interval_s", path
    )
    max_commits_per_run = _require_positive_int(
        wdata.get("max_commits_per_run", 50), "worker.max_commits_per_run", path
    )
    noise = _parse_regex_list(
        wdata.get("noise_subject_patterns", []),
        "worker.noise_subject_patterns",
        path,
    )
    skip = _parse_regex_list(
        wdata.get("skip_subject_patterns", []),
        "worker.skip_subject_patterns",
        path,
    )
    stop_on_failure = _require_bool(
        wdata.get("stop_on_failure", True), "worker.stop_on_failure", path
    )
    return WorkerConfig(
        poll_interval_s=poll_interval_s,
        noise_subject_patterns=noise,
        skip_subject_patterns=skip,
        max_commits_per_run=max_commits_per_run,
        stop_on_failure=stop_on_failure,
    )


def _parse_repos(value: object, path: Path) -> tuple[RepoConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(path, "repos: must be a list")
    out: list[RepoConfig] = []
    seen: set[str] = set()
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ConfigError(path, f"repos[{i}]: must be a mapping")
        repo = _parse_repo(cast(dict[str, object], entry), i, path)
        if repo.name in seen:
            raise ConfigError(path, f"repos[{i}].name: duplicate name {repo.name!r}")
        seen.add(repo.name)
        out.append(repo)
    return tuple(out)


def _parse_repo(entry: dict[str, object], i: int, path: Path) -> RepoConfig:
    unknown = set(entry) - ALLOWED_REPO_KEYS
    if unknown:
        raise ConfigError(path, f"repos[{i}]: unknown key(s): {sorted(unknown, key=str)}")

    if "name" not in entry:
        raise ConfigError(path, f"repos[{i}].name: missing required field")
    name_raw = entry["name"]
    if not isinstance(name_raw, str):
        raise ConfigError(path, f"repos[{i}].name: must be a string")
    if not SLUG_RE.match(name_raw):
        raise ConfigError(path, f"repos[{i}].name: not a valid slug ({name_raw!r})")

    if "url" not in entry:
        raise ConfigError(path, f"repos[{i}].url: missing required field")
    url_raw = entry["url"]
    if not isinstance(url_raw, str) or not url_raw:
        raise ConfigError(path, f"repos[{i}].url: must be a non-empty string")

    branch_raw = entry.get("branch", "main")
    if not isinstance(branch_raw, str) or not branch_raw:
        raise ConfigError(path, f"repos[{i}].branch: must be a non-empty string")

    start_commit = _parse_optional_nonempty_str(
        entry.get("start_commit"), f"repos[{i}].start_commit", path
    )
    model = _parse_optional_nonempty_str(entry.get("model"), f"repos[{i}].model", path)
    budget_usd = _parse_optional_budget(entry.get("budget_usd"), f"repos[{i}].budget_usd", path)
    exclude = _parse_str_list(entry.get("exclude", []), f"repos[{i}].exclude", path)

    return RepoConfig(
        name=name_raw,
        url=url_raw,
        branch=branch_raw,
        start_commit=start_commit,
        model=model,
        budget_usd=budget_usd,
        exclude=exclude,
    )


def _require_nonempty_str(value: object, key: str, path: Path) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(path, f"{key}: must be a non-empty string")
    return value


def _parse_optional_nonempty_str(value: object, key: str, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(path, f"{key}: must be a non-empty string or null")
    return value


def _require_positive_int(value: object, key: str, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(path, f"{key}: must be an integer")
    if value <= 0:
        raise ConfigError(path, f"{key}: must be > 0")
    return value


def _require_bool(value: object, key: str, path: Path) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(path, f"{key}: must be a boolean")
    return value


def _parse_optional_budget(value: object, key: str, path: Path) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(path, f"{key}: must be a number or null")
    coerced = float(value)
    if coerced < 0:
        raise ConfigError(path, f"{key}: must be >= 0")
    return coerced


def _parse_regex_list(value: object, key: str, path: Path) -> tuple[str, ...]:
    items = _parse_str_list(value, key, path)
    for i, pattern in enumerate(items):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(path, f"{key}[{i}]: invalid regex {pattern!r}: {exc}") from exc
    return items


def _parse_str_list(value: object, key: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(path, f"{key}: must be a list of strings")
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ConfigError(path, f"{key}[{i}]: must be a non-empty string")
        out.append(item)
    return tuple(out)
