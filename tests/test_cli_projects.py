"""End-to-end tests for the `cadence-memory projects` subcommand group."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.config import load_config
from cadence_memory.defaults.exclude import DEFAULT_PROJECT_EXCLUDE

runner = CliRunner()


def _make_store(tmp_path: Path, body: str = "projects: []\n") -> Path:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "config.yaml").write_text(body, encoding="utf-8")
    (store_dir / "annotations-config.yaml").write_text("documents: []\n", encoding="utf-8")
    return store_dir


def _make_project_dir(tmp_path: Path, name: str = "proj") -> Path:
    project_dir = tmp_path / name
    project_dir.mkdir()
    return project_dir


def test_projects_add_happy_path(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    project_dir = _make_project_dir(tmp_path)

    result = runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)]
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert f"added project: demo -> {project_dir.resolve()}" in result.stdout
    cfg = load_config(store_dir / "config.yaml")
    assert [p.name for p in cfg.projects] == ["demo"]
    assert cfg.projects[0].path == project_dir.resolve()


def test_projects_add_duplicate_fails(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    project_dir = _make_project_dir(tmp_path)

    first = runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)]
    )
    assert first.exit_code == 0, first.output + first.stderr

    second = runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)]
    )

    assert second.exit_code == 1
    assert "error: project already exists: demo" in second.stderr


def test_projects_add_missing_path_fails(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    missing = tmp_path / "does-not-exist"

    result = runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "demo", str(missing)]
    )

    assert result.exit_code == 1
    assert "error: path is not a directory" in result.stderr


def test_projects_add_invalid_name_fails(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    project_dir = _make_project_dir(tmp_path)

    result = runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "Bad Name", str(project_dir)]
    )

    assert result.exit_code == 1
    assert "error: invalid project name: Bad Name" in result.stderr


def test_projects_add_no_default_exclude_writes_empty_list(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    project_dir = _make_project_dir(tmp_path)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "add",
            "demo",
            str(project_dir),
            "--no-default-exclude",
        ],
    )

    assert result.exit_code == 0, result.output + result.stderr
    cfg = load_config(store_dir / "config.yaml")
    assert cfg.projects[0].exclude == ()
    raw = (store_dir / "config.yaml").read_text(encoding="utf-8")
    assert "exclude: []" in raw


def test_projects_add_default_writes_full_default_exclude(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    project_dir = _make_project_dir(tmp_path)

    result = runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)]
    )

    assert result.exit_code == 0, result.output + result.stderr
    cfg = load_config(store_dir / "config.yaml")
    assert cfg.projects[0].exclude == DEFAULT_PROJECT_EXCLUDE


def test_projects_list_empty(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "projects", "list"])

    assert result.exit_code == 0, result.output + result.stderr
    assert "(no projects registered)" in result.stderr
    assert result.stdout.strip() == ""


def test_projects_list_table_contains_headers_and_rows(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    project_dir = _make_project_dir(tmp_path)
    runner.invoke(app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)])

    result = runner.invoke(
        app, ["--store", str(store_dir), "projects", "list", "--format", "table"]
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert "name" in result.stdout
    assert "path" in result.stdout
    assert "excludes" in result.stdout
    assert "demo" in result.stdout
    assert str(project_dir.resolve()) in result.stdout


def test_projects_list_json_returns_array(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    project_dir = _make_project_dir(tmp_path)
    runner.invoke(app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)])

    result = runner.invoke(
        app, ["--store", str(store_dir), "projects", "list", "--format", "json"]
    )

    assert result.exit_code == 0, result.output + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 1
    entry = payload[0]
    assert entry["name"] == "demo"
    assert entry["path"] == str(project_dir.resolve())
    assert entry["excludes"] == len(DEFAULT_PROJECT_EXCLUDE)


def test_projects_remove_happy_path(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    project_dir = _make_project_dir(tmp_path)
    runner.invoke(app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)])

    result = runner.invoke(app, ["--store", str(store_dir), "projects", "remove", "demo"])

    assert result.exit_code == 0, result.output + result.stderr
    assert "removed project: demo" in result.stdout
    cfg = load_config(store_dir / "config.yaml")
    assert cfg.projects == ()


def test_projects_remove_missing_fails(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "projects", "remove", "nope"])

    assert result.exit_code == 1
    assert "error: project not found: nope" in result.stderr


def test_projects_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["projects", "--help"])

    assert result.exit_code == 0, result.output
    assert "add" in result.output
    assert "list" in result.output
    assert "remove" in result.output
    assert "autodetect" in result.output


def _make_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    (repo / ".git").mkdir(parents=True)
    return repo


def test_projects_autodetect_dry_run_lists_candidates(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    repo_a = _make_repo(code, "a")
    repo_b = _make_repo(code, "b")

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "projects", "autodetect", "--root", str(code)],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert f"+ a -> {repo_a.resolve()}" in result.stdout
    assert f"+ b -> {repo_b.resolve()}" in result.stdout
    assert "autodetect: 2 candidate(s); rerun with --apply to write" in result.stderr
    cfg = load_config(store_dir / "config.yaml")
    assert cfg.projects == ()


def test_projects_autodetect_apply_adds_entries(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    repo_a = _make_repo(code, "a")
    repo_b = _make_repo(code, "b")

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(code),
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert f"added project: a -> {repo_a.resolve()}" in result.stdout
    assert f"added project: b -> {repo_b.resolve()}" in result.stdout
    assert "autodetect: added 2 of 2" in result.stderr
    cfg = load_config(store_dir / "config.yaml")
    assert sorted(p.name for p in cfg.projects) == ["a", "b"]
    assert {p.path for p in cfg.projects} == {repo_a.resolve(), repo_b.resolve()}


def test_projects_autodetect_apply_idempotent(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _make_repo(code, "a")
    _make_repo(code, "b")

    first = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(code),
            "--apply",
        ],
    )
    assert first.exit_code == 0, first.output + first.stderr

    second = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(code),
            "--apply",
        ],
    )

    assert second.exit_code == 0, second.output + second.stderr
    assert "warn: project already exists: a" in second.stderr
    assert "warn: project already exists: b" in second.stderr
    assert "autodetect: added 0 of 2" in second.stderr
    cfg = load_config(store_dir / "config.yaml")
    assert sorted(p.name for p in cfg.projects) == ["a", "b"]


def test_projects_autodetect_respects_depth(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    deep = code / "a" / "b" / "c" / "d"
    (deep / ".git").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(code),
        ],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert "autodetect: 0 candidate(s)" in result.stderr
    assert result.stdout.strip() == ""


def test_projects_autodetect_skips_excluded_basenames(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    nm = code / "node_modules" / "pkg"
    (nm / ".git").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(code),
        ],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert "autodetect: 0 candidate(s)" in result.stderr
    assert result.stdout.strip() == ""


def test_projects_autodetect_slugifies_directory_names(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _make_repo(code, "My-Project")
    _make_repo(code, "weird name!")
    _make_repo(code, "123-good")

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(code),
        ],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert "+ my-project ->" in result.stdout
    assert "+ weird_name ->" in result.stdout
    assert "+ 123-good ->" in result.stdout


def test_projects_add_malformed_config_fails(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path, body="this is: : not: yaml\n")
    project_dir = _make_project_dir(tmp_path)

    result = runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)]
    )

    assert result.exit_code == 1
    assert "error: failed to parse config.yaml" in result.stderr


def test_projects_list_malformed_config_fails(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path, body="projects: not-a-list\n")

    result = runner.invoke(app, ["--store", str(store_dir), "projects", "list"])

    assert result.exit_code == 1
    assert "error:" in result.stderr


def test_projects_add_refuses_non_list_projects_value(tmp_path: Path) -> None:
    body = "projects: not-a-list\ndefaults:\n  kind: doc\n"
    store_dir = _make_store(tmp_path, body=body)
    project_dir = _make_project_dir(tmp_path)

    result = runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "demo", str(project_dir)]
    )

    assert result.exit_code == 1
    assert "projects must be a list" in result.stderr
    assert (store_dir / "config.yaml").read_text(encoding="utf-8") == body


def test_projects_remove_refuses_non_list_projects_value(tmp_path: Path) -> None:
    body = "projects: not-a-list\ndefaults:\n  kind: doc\n"
    store_dir = _make_store(tmp_path, body=body)

    result = runner.invoke(app, ["--store", str(store_dir), "projects", "remove", "demo"])

    assert result.exit_code == 1
    assert "projects must be a list" in result.stderr
    assert (store_dir / "config.yaml").read_text(encoding="utf-8") == body


def test_projects_autodetect_negative_depth_fails(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    code = tmp_path / "code"
    code.mkdir()

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(code),
            "--depth",
            "-1",
        ],
    )

    assert result.exit_code == 1
    assert "error: --depth must be >= 0" in result.stderr


def test_projects_autodetect_root_not_a_directory_fails(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    not_dir = tmp_path / "file.txt"
    not_dir.write_text("hi", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(not_dir),
        ],
    )

    assert result.exit_code == 1
    assert "error: --root is not a directory" in result.stderr


def test_projects_autodetect_apply_zero_when_all_registered(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    repo_a = _make_repo(code, "a")
    repo_b = _make_repo(code, "b")

    runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "a", str(repo_a)]
    )
    runner.invoke(
        app, ["--store", str(store_dir), "projects", "add", "b", str(repo_b)]
    )

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "projects",
            "autodetect",
            "--root",
            str(code),
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert "warn: project already exists: a" in result.stderr
    assert "warn: project already exists: b" in result.stderr
    assert "autodetect: added 0 of 2" in result.stderr
