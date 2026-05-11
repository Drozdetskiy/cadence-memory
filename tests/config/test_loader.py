"""Full matrix of tests for the v2 config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from cadence_memory.config import (
    Config,
    ConfigError,
    RepoConfig,
    WorkerConfig,
    load_config,
    parse_config,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_minimal_loads() -> None:
    cfg = load_config(FIXTURES / "minimal.yaml")
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.budget_usd == 0.50
    assert cfg.idle_timeout_s == 300
    assert cfg.worker == WorkerConfig()
    assert cfg.raw_auto_ingest is False
    assert cfg.repos == (RepoConfig(name="project-a", url="git@github.com:org/a.git"),)


def test_full_loads() -> None:
    cfg = load_config(FIXTURES / "full.yaml")
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.budget_usd == 0.50
    assert cfg.idle_timeout_s == 300
    assert cfg.raw_auto_ingest is False

    expected_worker = WorkerConfig(
        poll_interval_s=3600,
        noise_subject_patterns=(
            r"^chore\(deps\):",
            r"^style:",
            r"^Bump ",
            r"^Apply formatter",
        ),
        skip_subject_patterns=(r"^Merge branch ",),
        max_commits_per_run=50,
    )
    assert cfg.worker == expected_worker
    assert isinstance(cfg.worker.noise_subject_patterns, tuple)
    assert isinstance(cfg.worker.skip_subject_patterns, tuple)

    assert len(cfg.repos) == 2
    assert cfg.repos[0] == RepoConfig(
        name="project-a",
        url="git@github.com:org/project-a.git",
        branch="main",
        start_commit="abc1234",
        model="claude-opus-4-7",
        budget_usd=1.00,
        exclude=("**/*.lock", "node_modules/**", "vendor/**"),
    )
    assert cfg.repos[1] == RepoConfig(
        name="project-b",
        url="https://github.com/org/project-b",
        branch="develop",
    )


def test_empty_file_uses_all_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "empty.yaml"
    cfg_path.write_text("", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg == Config()


def test_unknown_top_key_errors() -> None:
    fixture = FIXTURES / "unknown_key.yaml"
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    assert "garbage" in exc_info.value.message
    assert exc_info.value.path == fixture


def test_unknown_repo_key_errors() -> None:
    data = {"repos": [{"name": "project-a", "url": "git@x:a.git", "weird": 1}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "repos[0]" in exc_info.value.message
    assert "weird" in exc_info.value.message


def test_bad_slug_errors() -> None:
    fixture = FIXTURES / "bad_slug.yaml"
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    assert "repos[0].name" in exc_info.value.message
    assert "Bad Name" in exc_info.value.message
    assert exc_info.value.path == fixture


def test_slug_accepts_alphanumeric() -> None:
    data = {"repos": [{"name": "ok-slug-2", "url": "git@x:a.git"}]}
    cfg = parse_config(data, path=Path("<test>"))
    assert cfg.repos[0].name == "ok-slug-2"


def test_duplicate_repo_name_errors() -> None:
    data = {
        "repos": [
            {"name": "project-a", "url": "git@x:a.git"},
            {"name": "project-a", "url": "git@x:b.git"},
        ],
    }
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "project-a" in exc_info.value.message
    assert "duplicate" in exc_info.value.message


def test_missing_url_errors() -> None:
    fixture = FIXTURES / "missing_url.yaml"
    with pytest.raises(ConfigError) as exc_info:
        load_config(fixture)
    assert "repos[0].url" in exc_info.value.message
    assert exc_info.value.path == fixture


def test_missing_name_errors() -> None:
    data = {"repos": [{"url": "git@x:a.git"}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "repos[0].name" in exc_info.value.message


def test_invalid_regex_errors() -> None:
    data = {"worker": {"noise_subject_patterns": ["[unclosed"]}}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "worker.noise_subject_patterns" in exc_info.value.message
    assert "[unclosed" in exc_info.value.message


def test_negative_budget_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"budget_usd": -1}, path=Path("<test>"))
    assert "budget_usd" in exc_info.value.message

    data = {"repos": [{"name": "project-a", "url": "git@x:a.git", "budget_usd": -0.1}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "repos[0].budget_usd" in exc_info.value.message


def test_budget_none_allowed() -> None:
    cfg = parse_config({"budget_usd": None}, path=Path("<test>"))
    assert cfg.budget_usd is None


def test_zero_timeout_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"idle_timeout_s": 0}, path=Path("<test>"))
    assert "idle_timeout_s" in exc_info.value.message

    with pytest.raises(ConfigError) as exc_info:
        parse_config({"worker": {"poll_interval_s": 0}}, path=Path("<test>"))
    assert "worker.poll_interval_s" in exc_info.value.message

    with pytest.raises(ConfigError) as exc_info:
        parse_config({"worker": {"max_commits_per_run": 0}}, path=Path("<test>"))
    assert "worker.max_commits_per_run" in exc_info.value.message


def test_non_mapping_top_level_errors(tmp_path: Path) -> None:
    cfg_path = tmp_path / "list.yaml"
    cfg_path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_path)
    assert "must be a mapping" in exc_info.value.message
    assert exc_info.value.path == cfg_path


def test_file_not_found_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfigError) as exc_info:
        load_config(missing)
    assert exc_info.value.path == missing
    assert "file not found" in exc_info.value.message


def test_invalid_yaml_errors(tmp_path: Path) -> None:
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("key: : :\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_path)
    assert exc_info.value.path == cfg_path
    assert "invalid YAML" in exc_info.value.message


def test_repos_not_list_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"repos": {}}, path=Path("<test>"))
    assert "repos" in exc_info.value.message
    assert "list" in exc_info.value.message


def test_exclude_non_string_errors() -> None:
    data_empty = {
        "repos": [{"name": "project-a", "url": "git@x:a.git", "exclude": [""]}],
    }
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data_empty, path=Path("<test>"))
    assert "repos[0].exclude" in exc_info.value.message

    data_int = {
        "repos": [{"name": "project-a", "url": "git@x:a.git", "exclude": [123]}],
    }
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data_int, path=Path("<test>"))
    assert "repos[0].exclude" in exc_info.value.message


def test_exclude_not_a_list_errors() -> None:
    data = {"repos": [{"name": "project-a", "url": "git@x:a.git", "exclude": "single"}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "repos[0].exclude" in exc_info.value.message
    assert "list of strings" in exc_info.value.message


def test_worker_not_mapping_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"worker": 42}, path=Path("<test>"))
    assert "worker" in exc_info.value.message
    assert "mapping" in exc_info.value.message


def test_unknown_worker_key_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"worker": {"bogus": 1}}, path=Path("<test>"))
    assert "worker" in exc_info.value.message
    assert "bogus" in exc_info.value.message


def test_repo_entry_not_mapping_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"repos": ["just-a-string"]}, path=Path("<test>"))
    assert "repos[0]" in exc_info.value.message
    assert "mapping" in exc_info.value.message


def test_repo_name_not_string_errors() -> None:
    data = {"repos": [{"name": 123, "url": "git@x:a.git"}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "repos[0].name" in exc_info.value.message
    assert "string" in exc_info.value.message


def test_repo_url_not_string_errors() -> None:
    data = {"repos": [{"name": "project-a", "url": 7}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "repos[0].url" in exc_info.value.message


def test_repo_url_empty_string_errors() -> None:
    data = {"repos": [{"name": "project-a", "url": ""}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data, path=Path("<test>"))
    assert "repos[0].url" in exc_info.value.message


def test_repo_branch_invalid_errors() -> None:
    data_int = {"repos": [{"name": "project-a", "url": "git@x:a.git", "branch": 1}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data_int, path=Path("<test>"))
    assert "repos[0].branch" in exc_info.value.message

    data_empty = {"repos": [{"name": "project-a", "url": "git@x:a.git", "branch": ""}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data_empty, path=Path("<test>"))
    assert "repos[0].branch" in exc_info.value.message


def test_repo_start_commit_invalid_errors() -> None:
    data_int = {"repos": [{"name": "project-a", "url": "git@x:a.git", "start_commit": 1}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data_int, path=Path("<test>"))
    assert "repos[0].start_commit" in exc_info.value.message

    data_empty = {"repos": [{"name": "project-a", "url": "git@x:a.git", "start_commit": ""}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data_empty, path=Path("<test>"))
    assert "repos[0].start_commit" in exc_info.value.message


def test_repo_model_invalid_errors() -> None:
    data_int = {"repos": [{"name": "project-a", "url": "git@x:a.git", "model": 1}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data_int, path=Path("<test>"))
    assert "repos[0].model" in exc_info.value.message

    data_empty = {"repos": [{"name": "project-a", "url": "git@x:a.git", "model": ""}]}
    with pytest.raises(ConfigError) as exc_info:
        parse_config(data_empty, path=Path("<test>"))
    assert "repos[0].model" in exc_info.value.message


def test_top_level_model_invalid_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"model": 42}, path=Path("<test>"))
    assert "model" in exc_info.value.message

    with pytest.raises(ConfigError) as exc_info:
        parse_config({"model": ""}, path=Path("<test>"))
    assert "model" in exc_info.value.message


def test_non_integer_timeout_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"idle_timeout_s": True}, path=Path("<test>"))
    assert "idle_timeout_s" in exc_info.value.message
    assert "integer" in exc_info.value.message

    with pytest.raises(ConfigError) as exc_info:
        parse_config({"idle_timeout_s": 1.5}, path=Path("<test>"))
    assert "idle_timeout_s" in exc_info.value.message

    with pytest.raises(ConfigError) as exc_info:
        parse_config({"idle_timeout_s": "5"}, path=Path("<test>"))
    assert "idle_timeout_s" in exc_info.value.message


def test_non_bool_raw_auto_ingest_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"raw_auto_ingest": "true"}, path=Path("<test>"))
    assert "raw_auto_ingest" in exc_info.value.message
    assert "boolean" in exc_info.value.message

    with pytest.raises(ConfigError) as exc_info:
        parse_config({"raw_auto_ingest": 1}, path=Path("<test>"))
    assert "raw_auto_ingest" in exc_info.value.message


def test_unknown_keys_with_mixed_types_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({1: "value", "garbage": True}, path=Path("<test>"))
    assert "garbage" in exc_info.value.message
    assert "1" in exc_info.value.message


def test_non_number_budget_errors() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_config({"budget_usd": True}, path=Path("<test>"))
    assert "budget_usd" in exc_info.value.message
    assert "number" in exc_info.value.message

    with pytest.raises(ConfigError) as exc_info:
        parse_config({"budget_usd": "free"}, path=Path("<test>"))
    assert "budget_usd" in exc_info.value.message
