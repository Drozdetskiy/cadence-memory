"""Config and AnnotationsConfig dataclasses, YAML loading, validation, ~ expansion."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from cadence_memory.documents import ids as document_ids


class ConfigError(ValueError):
    """Raised when a config file fails validation."""


@dataclass(frozen=True, slots=True)
class KindRule:
    pattern: str
    kind: str


@dataclass(frozen=True, slots=True)
class DiscoverConfig:
    kind_rules: tuple[KindRule, ...]


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    name: str
    path: Path
    exclude: tuple[str, ...]
    discover: DiscoverConfig


@dataclass(frozen=True, slots=True)
class GlobalsConfig:
    include: tuple[str, ...]
    exclude: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Defaults:
    kind: str


@dataclass(frozen=True, slots=True)
class ClaudeConfig:
    default_model: str = "claude-haiku-4-5"


@dataclass(frozen=True, slots=True)
class EnrichmentConfig:
    enabled: bool = True
    model: str | None = None


@dataclass(frozen=True, slots=True)
class ExpansionConfig:
    enabled: bool = True
    model: str | None = None
    max_variants: int = 3


@dataclass(frozen=True, slots=True)
class QueryConfig:
    expansion: ExpansionConfig = ExpansionConfig()


@dataclass(frozen=True, slots=True)
class Config:
    projects: tuple[ProjectConfig, ...]
    globals: GlobalsConfig
    defaults: Defaults
    commit_index: bool
    claude: ClaudeConfig = ClaudeConfig()
    enrichment: EnrichmentConfig = EnrichmentConfig()
    query: QueryConfig = QueryConfig()


def effective_enrichment_model(config: Config) -> str:
    """Return enrichment model: explicit override or claude.default_model fallback."""
    return config.enrichment.model or config.claude.default_model


def effective_expansion_model(config: Config) -> str:
    """Return query-expansion model: explicit override or claude.default_model fallback."""
    return config.query.expansion.model or config.claude.default_model


@dataclass(frozen=True, slots=True)
class DocumentEntry:
    id: str
    project: str | None
    path: str
    kind: str | None
    title: str | None
    tags: tuple[str, ...]
    related: tuple[str, ...]
    optional: bool


@dataclass(frozen=True, slots=True)
class AnnotationsConfig:
    documents: tuple[DocumentEntry, ...]


_PROJECT_NAME_RE: Final = re.compile(r"^[a-z0-9_-]+$")
_VALID_KINDS: Final[frozenset[str]] = frozenset(
    {"service", "pattern", "adr", "glossary", "task", "doc", "api-spec"}
)
_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {"projects", "globals", "defaults", "commit_index", "claude", "enrichment", "query"}
)
_PROJECT_KEYS: Final[frozenset[str]] = frozenset({"name", "path", "exclude", "discover"})
_DISCOVER_KEYS: Final[frozenset[str]] = frozenset({"kind_rules"})
_KIND_RULE_KEYS: Final[frozenset[str]] = frozenset({"pattern", "kind"})
_GLOBALS_KEYS: Final[frozenset[str]] = frozenset({"include", "exclude"})
_DEFAULTS_KEYS: Final[frozenset[str]] = frozenset({"kind"})
_CLAUDE_KEYS: Final[frozenset[str]] = frozenset({"default_model"})
_ENRICHMENT_KEYS: Final[frozenset[str]] = frozenset({"enabled", "model"})
_QUERY_KEYS: Final[frozenset[str]] = frozenset({"expansion"})
_EXPANSION_KEYS: Final[frozenset[str]] = frozenset({"enabled", "model", "max_variants"})
_ANNOTATIONS_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset({"documents"})
_DOCUMENT_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "project", "path", "kind", "title", "tags", "related", "optional"}
)

_DEFAULT_GLOBALS_INCLUDE: Final[tuple[str, ...]] = ("**/*.md",)
_DEFAULT_GLOBALS_EXCLUDE: Final[tuple[str, ...]] = (
    "ephemeral/**",
    "annotations-config.yaml*",
)
_DEFAULT_KIND: Final[str] = "doc"


def _err(prefix: str, field: str, reason: str) -> ConfigError:
    return ConfigError(f"{prefix}: {field} {reason}")


def _read_yaml(path: Path) -> object:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: malformed YAML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read file: {exc}") from exc


def _check_unknown_keys(
    prefix: str,
    field: str,
    data: dict[str, object],
    allowed: frozenset[str],
) -> None:
    extra = set(data.keys()) - allowed
    if extra:
        offending = sorted(extra)[0]
        suffix = f"{field}.{offending}" if field else offending
        raise _err(prefix, suffix, "is not a known key")


def _require_str(prefix: str, field: str, value: object) -> str:
    if not isinstance(value, str):
        raise _err(prefix, field, f"must be a string, got {type(value).__name__}")
    if value == "":
        raise _err(prefix, field, "must be a non-empty string")
    return value


def _require_str_list(prefix: str, field: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _err(prefix, field, f"must be a list of strings, got {type(value).__name__}")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise _err(
                prefix,
                f"{field}[{index}]",
                f"must be a string, got {type(item).__name__}",
            )
        if item == "":
            raise _err(prefix, f"{field}[{index}]", "must be a non-empty string")
        out.append(item)
    return tuple(out)


def _resolve_path(prefix: str, field: str, raw: str, config_dir: Path) -> Path:
    expanded = os.path.expanduser(raw)
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = (config_dir / candidate).resolve(strict=False)
    return candidate


def _parse_kind_rule(prefix: str, field: str, data: object) -> KindRule:
    if not isinstance(data, dict):
        raise _err(prefix, field, f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, field, data)
    _check_unknown_keys(prefix, field, typed, _KIND_RULE_KEYS)
    if "pattern" not in typed:
        raise _err(prefix, f"{field}.pattern", "is required")
    if "kind" not in typed:
        raise _err(prefix, f"{field}.kind", "is required")
    pattern = _require_str(prefix, f"{field}.pattern", typed["pattern"])
    kind = _require_str(prefix, f"{field}.kind", typed["kind"])
    if kind not in _VALID_KINDS:
        raise _err(
            prefix,
            f"{field}.kind",
            f"must be one of {sorted(_VALID_KINDS)}, got {kind!r}",
        )
    return KindRule(pattern=pattern, kind=kind)


def _parse_discover(prefix: str, field: str, data: object) -> DiscoverConfig:
    if data is None:
        return DiscoverConfig(kind_rules=())
    if not isinstance(data, dict):
        raise _err(prefix, field, f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, field, data)
    _check_unknown_keys(prefix, field, typed, _DISCOVER_KEYS)
    raw_rules = typed.get("kind_rules")
    if raw_rules is None:
        return DiscoverConfig(kind_rules=())
    if not isinstance(raw_rules, list):
        raise _err(
            prefix,
            f"{field}.kind_rules",
            f"must be a list, got {type(raw_rules).__name__}",
        )
    rules = tuple(
        _parse_kind_rule(prefix, f"{field}.kind_rules[{index}]", rule)
        for index, rule in enumerate(raw_rules)
    )
    return DiscoverConfig(kind_rules=rules)


def _parse_project(
    prefix: str,
    index: int,
    data: object,
    config_dir: Path,
) -> ProjectConfig:
    field = f"projects[{index}]"
    if not isinstance(data, dict):
        raise _err(prefix, field, f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, field, data)
    _check_unknown_keys(prefix, field, typed, _PROJECT_KEYS)
    if "name" not in typed:
        raise _err(prefix, f"{field}.name", "is required")
    if "path" not in typed:
        raise _err(prefix, f"{field}.path", "is required")
    name = _require_str(prefix, f"{field}.name", typed["name"])
    if not _PROJECT_NAME_RE.match(name):
        raise _err(
            prefix,
            f"{field}.name",
            f"must match ^[a-z0-9_-]+$, got {name!r}",
        )
    raw_path = _require_str(prefix, f"{field}.path", typed["path"])
    path = _resolve_path(prefix, f"{field}.path", raw_path, config_dir)
    exclude_raw = typed.get("exclude")
    exclude = (
        _require_str_list(prefix, f"{field}.exclude", exclude_raw)
        if exclude_raw is not None
        else ()
    )
    discover = _parse_discover(prefix, f"{field}.discover", typed.get("discover"))
    return ProjectConfig(name=name, path=path, exclude=exclude, discover=discover)


def _parse_globals(prefix: str, data: object) -> GlobalsConfig:
    if data is None:
        return GlobalsConfig(
            include=_DEFAULT_GLOBALS_INCLUDE,
            exclude=_DEFAULT_GLOBALS_EXCLUDE,
        )
    if not isinstance(data, dict):
        raise _err(prefix, "globals", f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, "globals", data)
    _check_unknown_keys(prefix, "globals", typed, _GLOBALS_KEYS)
    include_raw = typed.get("include")
    include = (
        _require_str_list(prefix, "globals.include", include_raw)
        if include_raw is not None
        else _DEFAULT_GLOBALS_INCLUDE
    )
    exclude_raw = typed.get("exclude")
    exclude = (
        _require_str_list(prefix, "globals.exclude", exclude_raw)
        if exclude_raw is not None
        else _DEFAULT_GLOBALS_EXCLUDE
    )
    return GlobalsConfig(include=include, exclude=exclude)


def _parse_claude(prefix: str, data: object) -> ClaudeConfig:
    if data is None:
        return ClaudeConfig()
    if not isinstance(data, dict):
        raise _err(prefix, "claude", f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, "claude", data)
    _check_unknown_keys(prefix, "claude", typed, _CLAUDE_KEYS)
    raw = typed.get("default_model")
    if raw is None:
        return ClaudeConfig()
    default_model = _require_str(prefix, "claude.default_model", raw)
    return ClaudeConfig(default_model=default_model)


def _parse_enrichment(prefix: str, data: object) -> EnrichmentConfig:
    if data is None:
        return EnrichmentConfig()
    if not isinstance(data, dict):
        raise _err(prefix, "enrichment", f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, "enrichment", data)
    _check_unknown_keys(prefix, "enrichment", typed, _ENRICHMENT_KEYS)

    enabled_raw = typed.get("enabled")
    if enabled_raw is None:
        enabled = True
    elif isinstance(enabled_raw, bool):
        enabled = enabled_raw
    else:
        raise _err(
            prefix,
            "enrichment.enabled",
            f"must be a boolean, got {type(enabled_raw).__name__}",
        )

    model_raw = typed.get("model")
    model = None if model_raw is None else _require_str(prefix, "enrichment.model", model_raw)

    return EnrichmentConfig(enabled=enabled, model=model)


def _parse_expansion(prefix: str, data: object) -> ExpansionConfig:
    if data is None:
        return ExpansionConfig()
    if not isinstance(data, dict):
        raise _err(
            prefix,
            "query.expansion",
            f"must be a mapping, got {type(data).__name__}",
        )
    typed = _as_str_keyed_dict(prefix, "query.expansion", data)
    _check_unknown_keys(prefix, "query.expansion", typed, _EXPANSION_KEYS)

    enabled_raw = typed.get("enabled")
    if enabled_raw is None:
        enabled = True
    elif isinstance(enabled_raw, bool):
        enabled = enabled_raw
    else:
        raise _err(
            prefix,
            "query.expansion.enabled",
            f"must be a boolean, got {type(enabled_raw).__name__}",
        )

    model_raw = typed.get("model")
    model = None if model_raw is None else _require_str(prefix, "query.expansion.model", model_raw)

    max_variants_raw = typed.get("max_variants")
    if max_variants_raw is None:
        max_variants = 3
    elif isinstance(max_variants_raw, bool) or not isinstance(max_variants_raw, int):
        raise _err(
            prefix,
            "query.expansion.max_variants",
            f"must be an integer, got {type(max_variants_raw).__name__}",
        )
    elif max_variants_raw <= 0:
        raise _err(
            prefix,
            "query.expansion.max_variants",
            f"must be a positive integer, got {max_variants_raw}",
        )
    else:
        max_variants = max_variants_raw

    return ExpansionConfig(enabled=enabled, model=model, max_variants=max_variants)


def _parse_query(prefix: str, data: object) -> QueryConfig:
    if data is None:
        return QueryConfig()
    if not isinstance(data, dict):
        raise _err(prefix, "query", f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, "query", data)
    _check_unknown_keys(prefix, "query", typed, _QUERY_KEYS)
    expansion = _parse_expansion(prefix, typed.get("expansion"))
    return QueryConfig(expansion=expansion)


def _parse_defaults(prefix: str, data: object) -> Defaults:
    if data is None:
        return Defaults(kind=_DEFAULT_KIND)
    if not isinstance(data, dict):
        raise _err(prefix, "defaults", f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, "defaults", data)
    _check_unknown_keys(prefix, "defaults", typed, _DEFAULTS_KEYS)
    kind_raw = typed.get("kind")
    kind = _DEFAULT_KIND if kind_raw is None else _require_str(prefix, "defaults.kind", kind_raw)
    return Defaults(kind=kind)


def _as_str_keyed_dict(prefix: str, field: str, data: dict[object, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            offending = field if field else "<top-level>"
            raise _err(
                prefix,
                offending,
                f"contains non-string key {key!r}",
            )
        out[key] = value
    return out


def load_config(path: Path) -> Config:
    """Load and validate a config.yaml file."""
    prefix = str(path)
    raw = _read_yaml(path)
    if raw is None:
        raise ConfigError(f"{prefix}: file is empty")
    if not isinstance(raw, dict):
        raise ConfigError(f"{prefix}: top-level must be a mapping, got {type(raw).__name__}")
    typed = _as_str_keyed_dict(prefix, "", raw)
    _check_unknown_keys(prefix, "", typed, _TOP_LEVEL_KEYS)

    config_dir = path.parent
    projects_raw = typed.get("projects")
    if projects_raw is None:
        projects: tuple[ProjectConfig, ...] = ()
    elif not isinstance(projects_raw, list):
        raise _err(prefix, "projects", f"must be a list, got {type(projects_raw).__name__}")
    else:
        parsed_projects: list[ProjectConfig] = []
        seen_names: set[str] = set()
        for index, project_raw in enumerate(projects_raw):
            project = _parse_project(prefix, index, project_raw, config_dir)
            if project.name in seen_names:
                raise _err(
                    prefix,
                    f"projects[{index}].name",
                    f"is a duplicate of an earlier project ({project.name!r})",
                )
            seen_names.add(project.name)
            parsed_projects.append(project)
        projects = tuple(parsed_projects)

    globals_cfg = _parse_globals(prefix, typed.get("globals"))
    defaults_cfg = _parse_defaults(prefix, typed.get("defaults"))

    commit_index_raw = typed.get("commit_index")
    if commit_index_raw is None:
        commit_index = False
    elif isinstance(commit_index_raw, bool):
        commit_index = commit_index_raw
    else:
        raise _err(
            prefix,
            "commit_index",
            f"must be a boolean, got {type(commit_index_raw).__name__}",
        )

    claude_cfg = _parse_claude(prefix, typed.get("claude"))
    enrichment_cfg = _parse_enrichment(prefix, typed.get("enrichment"))
    query_cfg = _parse_query(prefix, typed.get("query"))

    return Config(
        projects=projects,
        globals=globals_cfg,
        defaults=defaults_cfg,
        commit_index=commit_index,
        claude=claude_cfg,
        enrichment=enrichment_cfg,
        query=query_cfg,
    )


def _parse_document_entry(prefix: str, index: int, data: object) -> DocumentEntry:
    field = f"documents[{index}]"
    if not isinstance(data, dict):
        raise _err(prefix, field, f"must be a mapping, got {type(data).__name__}")
    typed = _as_str_keyed_dict(prefix, field, data)
    _check_unknown_keys(prefix, field, typed, _DOCUMENT_KEYS)

    if "id" not in typed:
        raise _err(prefix, f"{field}.id", "is required")
    if "path" not in typed:
        raise _err(prefix, f"{field}.path", "is required")

    raw_id = _require_str(prefix, f"{field}.id", typed["id"])
    try:
        parsed_id = document_ids.parse(raw_id)
    except ValueError as exc:
        raise _err(prefix, f"{field}.id", f"is invalid: {exc}") from exc

    raw_path = _require_str(prefix, f"{field}.path", typed["path"])

    project_raw = typed.get("project")
    project: str | None
    if parsed_id.source_type == "project":
        if project_raw is None:
            project = parsed_id.project
        else:
            project_value = _require_str(prefix, f"{field}.project", project_raw)
            if project_value != parsed_id.project:
                raise _err(
                    prefix,
                    f"{field}.project",
                    f"must match the project in id ({parsed_id.project!r}), got {project_value!r}",
                )
            project = project_value
    elif parsed_id.source_type == "global":
        if project_raw is not None:
            raise _err(
                prefix,
                f"{field}.project",
                "must be absent or null for a global document id",
            )
        project = None
    else:
        raise _err(
            prefix,
            f"{field}.id",
            "ephemeral ids are not allowed in annotations-config.yaml",
        )

    kind_raw = typed.get("kind")
    kind = None if kind_raw is None else _require_str(prefix, f"{field}.kind", kind_raw)

    title_raw = typed.get("title")
    title = None if title_raw is None else _require_str(prefix, f"{field}.title", title_raw)

    tags_raw = typed.get("tags")
    tags = _require_str_list(prefix, f"{field}.tags", tags_raw) if tags_raw is not None else ()

    related_raw = typed.get("related")
    related = (
        _require_str_list(prefix, f"{field}.related", related_raw)
        if related_raw is not None
        else ()
    )

    optional_raw = typed.get("optional")
    if optional_raw is None:
        optional = False
    elif isinstance(optional_raw, bool):
        optional = optional_raw
    else:
        raise _err(
            prefix,
            f"{field}.optional",
            f"must be a boolean, got {type(optional_raw).__name__}",
        )

    return DocumentEntry(
        id=raw_id,
        project=project,
        path=raw_path,
        kind=kind,
        title=title,
        tags=tags,
        related=related,
        optional=optional,
    )


def _resolve_document_path(
    entry: DocumentEntry, project_paths: dict[str, Path], store_dir: Path
) -> Path:
    rel = Path(entry.path)
    base = store_dir if entry.project is None else project_paths[entry.project]
    candidate = rel if rel.is_absolute() else base / rel
    return candidate.resolve(strict=False)


def load_annotations_config(path: Path, *, config: Config | None = None) -> AnnotationsConfig:
    """Load and validate an annotations-config.yaml file."""
    prefix = str(path)
    raw = _read_yaml(path)
    if raw is None:
        raise ConfigError(f"{prefix}: file is empty")
    if not isinstance(raw, dict):
        raise ConfigError(f"{prefix}: top-level must be a mapping, got {type(raw).__name__}")
    typed = _as_str_keyed_dict(prefix, "", raw)
    _check_unknown_keys(prefix, "", typed, _ANNOTATIONS_TOP_LEVEL_KEYS)

    documents_raw = typed.get("documents")
    if documents_raw is None:
        raise _err(prefix, "documents", "is required")
    if not isinstance(documents_raw, list):
        raise _err(
            prefix,
            "documents",
            f"must be a list, got {type(documents_raw).__name__}",
        )

    seen_ids: set[str] = set()
    parsed: list[DocumentEntry] = []
    for index, entry_raw in enumerate(documents_raw):
        entry = _parse_document_entry(prefix, index, entry_raw)
        if entry.id in seen_ids:
            raise _err(
                prefix,
                f"documents[{index}].id",
                f"is a duplicate of an earlier entry ({entry.id!r})",
            )
        seen_ids.add(entry.id)
        parsed.append(entry)

    if config is not None:
        project_paths = {project.name: project.path for project in config.projects}
        for index, entry in enumerate(parsed):
            if entry.project is not None and entry.project not in project_paths:
                raise _err(
                    prefix,
                    f"documents[{index}].project",
                    f"does not match any project in config.yaml ({entry.project!r})",
                )

        store_dir = path.parent
        seen_paths: dict[Path, int] = {}
        for index, entry in enumerate(parsed):
            resolved = _resolve_document_path(entry, project_paths, store_dir)
            if resolved in seen_paths:
                earlier = seen_paths[resolved]
                raise _err(
                    prefix,
                    f"documents[{index}]",
                    (f"resolves to the same physical path as documents[{earlier}] ({resolved})"),
                )
            seen_paths[resolved] = index

    return AnnotationsConfig(documents=tuple(parsed))
