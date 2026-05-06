"""Unit tests for config.yaml and annotations-config.yaml loaders."""

from __future__ import annotations

import dataclasses
from importlib import resources
from pathlib import Path

import pytest

from cadence_memory.config import (
    AnnotationsConfig,
    ClaudeConfig,
    Config,
    ConfigError,
    Defaults,
    DiscoverConfig,
    DocumentEntry,
    EnrichmentConfig,
    ExpansionConfig,
    GlobalsConfig,
    KindRule,
    ProjectConfig,
    QueryConfig,
    effective_enrichment_model,
    effective_expansion_model,
    load_annotations_config,
    load_config,
)

FIXTURES = Path(__file__).parent / "fixtures" / "configs"


def _fixture(name: str) -> Path:
    return FIXTURES / name


def test_load_config_full_happy_path() -> None:
    cfg = load_config(_fixture("config_valid_full.yaml"))

    assert isinstance(cfg, Config)
    assert len(cfg.projects) == 2

    billing = cfg.projects[0]
    assert isinstance(billing, ProjectConfig)
    assert billing.name == "billing"
    assert billing.path == Path("/tmp/cadence_memory_test/billing")
    assert billing.exclude == ("node_modules/**", ".venv/**", "**/test_*.md")
    assert isinstance(billing.discover, DiscoverConfig)
    assert billing.discover.kind_rules == (
        KindRule(pattern="README.md", kind="service"),
        KindRule(pattern="docs/adr/*.md", kind="adr"),
    )

    orders = cfg.projects[1]
    assert orders.name == "orders"
    assert orders.path == Path("/tmp/cadence_memory_test/orders")
    assert orders.exclude == ("node_modules/**", ".venv/**")
    assert orders.discover == DiscoverConfig(kind_rules=())

    assert cfg.globals == GlobalsConfig(
        include=("**/*.md", "**/*.markdown"),
        exclude=("ephemeral/**", "annotations-config.yaml*", "drafts/**"),
    )
    assert cfg.defaults == Defaults(kind="pattern")
    assert cfg.commit_index is True


def test_config_dataclasses_are_frozen() -> None:
    cfg = load_config(_fixture("config_valid_full.yaml"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.commit_index = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.projects[0].name = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.globals.include = ()  # type: ignore[misc]


def test_load_config_minimal_applies_defaults() -> None:
    cfg = load_config(_fixture("config_minimal.yaml"))

    assert len(cfg.projects) == 1
    project = cfg.projects[0]
    assert project.name == "billing"
    assert project.exclude == ()
    assert project.discover == DiscoverConfig(kind_rules=())

    assert cfg.globals.include == ("**/*.md",)
    assert cfg.globals.exclude == ("ephemeral/**", "annotations-config.yaml*")
    assert cfg.defaults.kind == "doc"
    assert cfg.commit_index is False
    assert cfg.claude == ClaudeConfig(default_model="claude-haiku-4-5")
    assert cfg.enrichment == EnrichmentConfig(enabled=True, model=None)


def test_load_config_unknown_top_key() -> None:
    fixture = _fixture("config_unknown_top_key.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "unexpected_root_key" in msg
    assert "not a known key" in msg


def test_load_config_unknown_project_key() -> None:
    fixture = _fixture("config_unknown_project_key.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "projects[0].bogus_key" in msg


def test_load_config_bad_name() -> None:
    fixture = _fixture("config_bad_name.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "projects[0].name" in msg
    assert "^[a-z0-9_-]+$" in msg


def test_load_config_missing_path() -> None:
    fixture = _fixture("config_missing_path.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "projects[0].path" in msg
    assert "required" in msg


def test_load_config_invalid_kind() -> None:
    fixture = _fixture("config_invalid_kind.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "kind_rules[0].kind" in msg
    assert "not_a_real_kind" in msg


def test_load_config_kind_rules_accepts_api_spec(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "projects:\n"
            "  - name: billing\n"
            "    path: /tmp/a\n"
            "    discover:\n"
            "      kind_rules:\n"
            '        - pattern: "**/openapi.md"\n'
            "          kind: api-spec\n"
        ),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.projects[0].discover.kind_rules == (
        KindRule(pattern="**/openapi.md", kind="api-spec"),
    )


def test_load_config_kind_rules_rejects_unknown_kind(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "projects:\n"
            "  - name: billing\n"
            "    path: /tmp/a\n"
            "    discover:\n"
            "      kind_rules:\n"
            '        - pattern: "**/foo.md"\n'
            "          kind: nonsense\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    msg = str(exc_info.value)
    assert "kind_rules[0].kind" in msg
    assert "nonsense" in msg


def test_load_config_malformed_yaml() -> None:
    fixture = _fixture("config_malformed.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "malformed YAML" in msg


def test_load_config_tilde_expansion() -> None:
    fixture = _fixture("config_with_tilde.yaml")
    cfg = load_config(fixture)
    project_path = cfg.projects[0].path
    assert project_path.is_absolute()
    assert project_path == Path.home() / "some" / "dir" / "billing"


def test_load_config_relative_path_resolved_against_config_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "projects:\n  - name: billing\n    path: ./relative-dir\n",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    project_path = cfg.projects[0].path
    assert project_path.is_absolute()
    assert project_path == (tmp_path / "relative-dir").resolve()


def test_load_config_duplicate_project_names_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: billing\n    path: /tmp/a\n  - name: billing\n    path: /tmp/b\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    msg = str(exc_info.value)
    assert "projects[1].name" in msg
    assert "duplicate" in msg


def test_load_config_top_level_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="top-level must be a mapping"):
        load_config(config_path)


def test_load_config_empty_file_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="file is empty"):
        load_config(config_path)


def test_load_config_commit_index_must_be_bool(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: billing\n    path: /tmp/billing\ncommit_index: maybe\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="commit_index"):
        load_config(config_path)


def test_load_config_exclude_must_be_string_list(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: billing\n    path: /tmp/billing\n    exclude:\n      - 42\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"projects\[0\]\.exclude\[0\]"):
        load_config(config_path)


def test_load_config_kind_rule_missing_pattern(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "projects:\n"
            "  - name: billing\n"
            "    path: /tmp/billing\n"
            "    discover:\n"
            "      kind_rules:\n"
            "        - kind: service\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"kind_rules\[0\]\.pattern"):
        load_config(config_path)


# --------------------------------------------------------------------------- #
# load_annotations_config                                                     #
# --------------------------------------------------------------------------- #


def test_load_annotations_full_happy_path() -> None:
    cfg = load_annotations_config(_fixture("annotations_valid_full.yaml"))

    assert isinstance(cfg, AnnotationsConfig)
    assert len(cfg.documents) == 5

    first = cfg.documents[0]
    assert isinstance(first, DocumentEntry)
    assert first.id == "billing:README.md"
    assert first.project == "billing"
    assert first.path == "README.md"
    assert first.kind == "service"
    assert first.title == "Billing Service"
    assert first.tags == ("payments", "stripe", "money")
    assert first.related == ("orders:README.md",)
    assert first.optional is False

    arch = cfg.documents[1]
    assert arch.kind == "pattern"
    assert arch.title == "Billing data model"
    assert arch.tags == ()
    assert arch.related == ()

    bare = cfg.documents[2]
    assert bare.id == "billing:docs/api.md"
    assert bare.kind is None
    assert bare.title is None
    assert bare.tags == ()
    assert bare.related == ()
    assert bare.optional is False

    orders = cfg.documents[3]
    assert orders.optional is True

    glob = cfg.documents[4]
    assert glob.id == ":workflows/deploy-process.md"
    assert glob.project is None
    assert glob.path == "workflows/deploy-process.md"
    assert glob.tags == ("ops",)


def test_load_annotations_dataclasses_are_frozen() -> None:
    cfg = load_annotations_config(_fixture("annotations_valid_full.yaml"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.documents[0].kind = "doc"  # type: ignore[misc]


def test_load_annotations_minimal_entry() -> None:
    cfg = load_annotations_config(_fixture("annotations_minimal.yaml"))
    assert len(cfg.documents) == 1
    entry = cfg.documents[0]
    assert entry.id == "billing:README.md"
    assert entry.project == "billing"
    assert entry.path == "README.md"
    assert entry.kind is None
    assert entry.title is None
    assert entry.tags == ()
    assert entry.related == ()
    assert entry.optional is False


def test_load_annotations_missing_id() -> None:
    fixture = _fixture("annotations_missing_id.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "documents[0].id" in msg
    assert "required" in msg


def test_load_annotations_missing_path() -> None:
    fixture = _fixture("annotations_missing_path.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "documents[0].path" in msg
    assert "required" in msg


def test_load_annotations_unknown_key() -> None:
    fixture = _fixture("annotations_unknown_key.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "documents[0].bogus_key" in msg
    assert "not a known key" in msg


def test_load_annotations_duplicate_id() -> None:
    fixture = _fixture("annotations_duplicate_id.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "documents[1].id" in msg
    assert "duplicate" in msg


def test_load_annotations_project_mismatch() -> None:
    fixture = _fixture("annotations_project_mismatch.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "documents[0].project" in msg
    assert "match" in msg


def test_load_annotations_bad_id_propagates() -> None:
    fixture = _fixture("annotations_bad_id.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "documents[0].id" in msg


def test_load_annotations_malformed_yaml() -> None:
    fixture = _fixture("annotations_malformed.yaml")
    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(fixture)
    msg = str(exc_info.value)
    assert str(fixture) in msg
    assert "malformed YAML" in msg


def test_load_annotations_top_level_must_be_mapping(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="top-level must be a mapping"):
        load_annotations_config(annotations_path)


def test_load_annotations_documents_required(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"documents is required"):
        load_annotations_config(annotations_path)


def test_load_annotations_unknown_top_level(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text("documents: []\nbogus_top: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="bogus_top"):
        load_annotations_config(annotations_path)


def test_load_annotations_optional_must_be_bool(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text(
        ("documents:\n  - id: billing:README.md\n    path: README.md\n    optional: maybe\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"documents\[0\]\.optional"):
        load_annotations_config(annotations_path)


def test_load_annotations_tags_must_be_string_list(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text(
        ("documents:\n  - id: billing:README.md\n    path: README.md\n    tags:\n      - 42\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"documents\[0\]\.tags\[0\]"):
        load_annotations_config(annotations_path)


def test_load_annotations_global_with_project_rejected(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text(
        (
            "documents:\n"
            '  - id: ":workflows/deploy.md"\n'
            "    project: billing\n"
            "    path: workflows/deploy.md\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"documents\[0\]\.project"):
        load_annotations_config(annotations_path)


def test_load_annotations_ephemeral_id_rejected(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text(
        ("documents:\n  - id: eph:scratchpad\n    path: ephemeral/scratchpad.md\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="ephemeral"):
        load_annotations_config(annotations_path)


def test_load_annotations_xref_unknown_project(tmp_path: Path) -> None:
    config = load_config(_fixture("annotations_for_xref/config.yaml"))
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text(
        ("documents:\n  - id: ghost:README.md\n    project: ghost\n    path: README.md\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(annotations_path, config=config)
    msg = str(exc_info.value)
    assert "documents[0].project" in msg
    assert "does not match any project" in msg


def test_load_annotations_xref_duplicate_physical_path(tmp_path: Path) -> None:
    config = load_config(_fixture("annotations_for_xref/config.yaml"))
    fixture = _fixture("annotations_duplicate_path.yaml")

    no_config_result = load_annotations_config(fixture)
    assert len(no_config_result.documents) == 2

    with pytest.raises(ConfigError) as exc_info:
        load_annotations_config(fixture, config=config)
    msg = str(exc_info.value)
    assert "documents[1]" in msg
    assert "physical path" in msg


def test_load_annotations_xref_happy_path(tmp_path: Path) -> None:
    config = load_config(_fixture("annotations_for_xref/config.yaml"))
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text(
        (
            "documents:\n"
            "  - id: billing:README.md\n"
            "    path: README.md\n"
            "  - id: orders:README.md\n"
            "    path: README.md\n"
            '  - id: ":workflows/deploy.md"\n'
            "    path: workflows/deploy.md\n"
        ),
        encoding="utf-8",
    )
    cfg = load_annotations_config(annotations_path, config=config)
    assert len(cfg.documents) == 3


# --------------------------------------------------------------------------- #
# Type-guard error paths (non-mapping / non-list / non-string)                #
# --------------------------------------------------------------------------- #


def test_load_config_unreadable_file_wrapped(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigError, match="cannot read file"):
        load_config(missing)


def test_load_config_non_string_top_level_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("1: foo\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    msg = str(exc_info.value)
    assert "<top-level>" in msg
    assert "non-string key" in msg


def test_load_config_projects_must_be_list(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: not-a-list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"projects must be a list"):
        load_config(config_path)


def test_load_config_projects_omitted_yields_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("commit_index: false\n", encoding="utf-8")
    cfg = load_config(config_path)
    assert cfg.projects == ()


def test_load_config_project_entry_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects:\n  - just-a-string\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"projects\[0\] must be a mapping"):
        load_config(config_path)


def test_load_config_project_missing_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects:\n  - path: /tmp/x\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"projects\[0\]\.name is required"):
        load_config(config_path)


def test_load_config_globals_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("globals: not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"globals must be a mapping"):
        load_config(config_path)


def test_load_config_defaults_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("defaults: not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"defaults must be a mapping"):
        load_config(config_path)


def test_load_config_discover_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: billing\n    path: /tmp/billing\n    discover: not-a-mapping\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"projects\[0\]\.discover must be a mapping"):
        load_config(config_path)


def test_load_config_kind_rules_must_be_list(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "projects:\n"
            "  - name: billing\n"
            "    path: /tmp/billing\n"
            "    discover:\n"
            "      kind_rules: not-a-list\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"kind_rules must be a list"):
        load_config(config_path)


def test_load_config_discover_kind_rules_absent_yields_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: billing\n    path: /tmp/billing\n    discover: {}\n"),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.projects[0].discover == DiscoverConfig(kind_rules=())


def test_load_config_kind_rule_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "projects:\n"
            "  - name: billing\n"
            "    path: /tmp/billing\n"
            "    discover:\n"
            "      kind_rules:\n"
            "        - just-a-string\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"kind_rules\[0\] must be a mapping"):
        load_config(config_path)


def test_load_config_kind_rule_missing_kind(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "projects:\n"
            "  - name: billing\n"
            "    path: /tmp/billing\n"
            "    discover:\n"
            "      kind_rules:\n"
            "        - pattern: README.md\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"kind_rules\[0\]\.kind is required"):
        load_config(config_path)


def test_load_config_name_must_be_string(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: 42\n    path: /tmp/x\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"projects\[0\]\.name must be a string"):
        load_config(config_path)


def test_load_config_name_must_be_non_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: ''\n    path: /tmp/x\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"projects\[0\]\.name must be a non-empty string"):
        load_config(config_path)


def test_load_config_exclude_must_be_list(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: billing\n    path: /tmp/x\n    exclude: not-a-list\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"projects\[0\]\.exclude must be a list of strings"):
        load_config(config_path)


def test_load_annotations_documents_must_be_list(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text("documents: not-a-list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"documents must be a list"):
        load_annotations_config(annotations_path)


def test_load_annotations_document_entry_must_be_mapping(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text("documents:\n  - just-a-string\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"documents\[0\] must be a mapping"):
        load_annotations_config(annotations_path)


def test_load_annotations_empty_file_rejected(tmp_path: Path) -> None:
    annotations_path = tmp_path / "annotations-config.yaml"
    annotations_path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="file is empty"):
        load_annotations_config(annotations_path)


def test_load_config_exclude_rejects_empty_string_item(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        ("projects:\n  - name: billing\n    path: /tmp/x\n    exclude:\n      - ''\n"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    msg = str(exc_info.value)
    assert "projects[0].exclude[0]" in msg
    assert "must be a non-empty string" in msg


def test_load_config_globals_partial_overrides_keep_other_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "globals:\n  include:\n    - 'docs/**/*.md'\n",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.globals.include == ("docs/**/*.md",)
    assert cfg.globals.exclude == ("ephemeral/**", "annotations-config.yaml*")


# --------------------------------------------------------------------------- #
# claude / enrichment sections                                                #
# --------------------------------------------------------------------------- #


def test_load_config_claude_and_enrichment_defaults_when_absent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: []\n", encoding="utf-8")
    cfg = load_config(config_path)
    assert cfg.claude == ClaudeConfig(default_model="claude-haiku-4-5")
    assert cfg.enrichment == EnrichmentConfig(enabled=True, model=None)
    assert effective_enrichment_model(cfg) == "claude-haiku-4-5"


def test_load_config_claude_default_model_override(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "claude:\n  default_model: claude-sonnet-4-6\n",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.claude.default_model == "claude-sonnet-4-6"
    assert effective_enrichment_model(cfg) == "claude-sonnet-4-6"


def test_load_config_enrichment_explicit_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "enrichment:\n  enabled: false\n  model: claude-opus-4-7\n",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.enrichment == EnrichmentConfig(enabled=False, model="claude-opus-4-7")
    assert effective_enrichment_model(cfg) == "claude-opus-4-7"


def test_load_config_enrichment_null_model_falls_back_to_claude(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "claude:\n"
            "  default_model: claude-haiku-foo\n"
            "enrichment:\n"
            "  enabled: true\n"
            "  model: null\n"
        ),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.enrichment.model is None
    assert effective_enrichment_model(cfg) == "claude-haiku-foo"


def test_load_config_claude_unknown_key_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("claude:\n  bogus: value\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    msg = str(exc_info.value)
    assert "claude.bogus" in msg
    assert "not a known key" in msg


def test_load_config_enrichment_unknown_key_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("enrichment:\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    msg = str(exc_info.value)
    assert "enrichment.bogus" in msg
    assert "not a known key" in msg


def test_load_config_claude_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("claude: not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"claude must be a mapping"):
        load_config(config_path)


def test_load_config_enrichment_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("enrichment: not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"enrichment must be a mapping"):
        load_config(config_path)


def test_load_config_enrichment_enabled_must_be_bool(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("enrichment:\n  enabled: maybe\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"enrichment\.enabled"):
        load_config(config_path)


def test_load_config_enrichment_model_must_be_string(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("enrichment:\n  model: 42\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"enrichment\.model"):
        load_config(config_path)


def test_load_config_claude_default_model_must_be_string(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("claude:\n  default_model: 42\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"claude\.default_model"):
        load_config(config_path)


# --------------------------------------------------------------------------- #
# query.expansion section                                                     #
# --------------------------------------------------------------------------- #


def test_load_config_query_expansion_defaults_when_absent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: []\n", encoding="utf-8")
    cfg = load_config(config_path)
    assert cfg.query == QueryConfig(
        expansion=ExpansionConfig(enabled=True, model=None, max_variants=3),
    )
    assert effective_expansion_model(cfg) == "claude-haiku-4-5"


def test_load_config_query_expansion_disabled_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "query:\n  expansion:\n    enabled: false\n",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.query.expansion.enabled is False
    assert cfg.query.expansion.model is None
    assert cfg.query.expansion.max_variants == 3


def test_load_config_query_expansion_explicit_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "query:\n"
            "  expansion:\n"
            "    enabled: true\n"
            "    model: claude-sonnet-4-6\n"
            "    max_variants: 5\n"
        ),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.query.expansion == ExpansionConfig(
        enabled=True, model="claude-sonnet-4-6", max_variants=5
    )
    assert effective_expansion_model(cfg) == "claude-sonnet-4-6"


def test_load_config_query_expansion_null_model_falls_back(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "claude:\n"
            "  default_model: claude-haiku-foo\n"
            "query:\n"
            "  expansion:\n"
            "    enabled: true\n"
            "    model: null\n"
        ),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.query.expansion.model is None
    assert effective_expansion_model(cfg) == "claude-haiku-foo"


def test_load_config_query_unknown_key_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("query:\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    msg = str(exc_info.value)
    assert "query.bogus" in msg
    assert "not a known key" in msg


def test_load_config_query_expansion_unknown_key_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "query:\n  expansion:\n    bogus: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    msg = str(exc_info.value)
    assert "query.expansion.bogus" in msg
    assert "not a known key" in msg


def test_load_config_query_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("query: not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"query must be a mapping"):
        load_config(config_path)


def test_load_config_query_expansion_must_be_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "query:\n  expansion: not-a-mapping\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"query\.expansion must be a mapping"):
        load_config(config_path)


def test_load_config_query_expansion_enabled_must_be_bool(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "query:\n  expansion:\n    enabled: maybe\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"query\.expansion\.enabled"):
        load_config(config_path)


def test_load_config_query_expansion_model_must_be_string(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "query:\n  expansion:\n    model: 42\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"query\.expansion\.model"):
        load_config(config_path)


def test_load_config_query_expansion_max_variants_must_be_int(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "query:\n  expansion:\n    max_variants: many\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"query\.expansion\.max_variants"):
        load_config(config_path)


def test_load_config_query_expansion_max_variants_must_be_positive(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "query:\n  expansion:\n    max_variants: 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"query\.expansion\.max_variants"):
        load_config(config_path)


def test_load_config_query_expansion_max_variants_rejects_bool(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "query:\n  expansion:\n    max_variants: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"query\.expansion\.max_variants"):
        load_config(config_path)


# --------------------------------------------------------------------------- #
# Embedded default templates copied by `cadence-memory init`                  #
# --------------------------------------------------------------------------- #


def _write_default(name: str, dst: Path) -> Path:
    text = resources.files("cadence_memory.defaults").joinpath(name).read_text(encoding="utf-8")
    dst.write_text(text, encoding="utf-8")
    return dst


def test_default_config_template_loads(tmp_path: Path) -> None:
    config_path = _write_default("config.yaml", tmp_path / "config.yaml")
    cfg = load_config(config_path)

    assert cfg.projects == ()
    assert cfg.globals == GlobalsConfig(
        include=("**/*.md",),
        exclude=("ephemeral/**", "annotations-config.yaml*"),
    )
    assert cfg.defaults == Defaults(kind="doc")
    assert cfg.commit_index is False
    assert cfg.claude == ClaudeConfig(default_model="claude-haiku-4-5")
    assert cfg.enrichment == EnrichmentConfig(enabled=True, model=None)
    assert effective_enrichment_model(cfg) == "claude-haiku-4-5"
    assert cfg.query == QueryConfig(
        expansion=ExpansionConfig(enabled=True, model=None, max_variants=3),
    )
    assert effective_expansion_model(cfg) == "claude-haiku-4-5"


def test_default_annotations_template_loads(tmp_path: Path) -> None:
    annotations_path = _write_default(
        "annotations-config.yaml", tmp_path / "annotations-config.yaml"
    )
    cfg = load_annotations_config(annotations_path)
    assert cfg == AnnotationsConfig(documents=())


def test_default_gitignore_matches_design() -> None:
    text = (
        resources.files("cadence_memory.defaults").joinpath("gitignore").read_text(encoding="utf-8")
    )
    entries = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert entries == [
        "index.sqlite",
        "index.sqlite-journal",
        "index.sqlite-wal",
        "index.sqlite-shm",
        "annotations-config.yaml.proposed",
        "ephemeral/",
        "*.tmp",
    ]
