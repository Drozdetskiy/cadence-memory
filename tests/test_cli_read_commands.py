"""End-to-end tests for the read-only CLI commands: list, query, get, show."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import _resolve_format, app

runner = CliRunner()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_seeded_store(tmp_path: Path) -> tuple[Path, Path]:
    """Create a store + project with three indexed documents."""
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()

    _write(project_dir / "alpha.md", "# Alpha\n\nuniqalpha body\n")
    _write(project_dir / "beta.md", "# Beta\n\nuniqbeta body shared\n")
    _write(project_dir / "gamma.md", "# Gamma\n\nuniqgamma extra body shared\n")

    config_yaml = f"projects:\n  - name: proj\n    path: {project_dir}\ndefaults:\n  kind: doc\n"
    (store_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")

    annotations_yaml = (
        "documents:\n"
        "  - id: proj:alpha.md\n"
        "    project: proj\n"
        "    path: alpha.md\n"
        "    kind: doc\n"
        "    tags: [first, common]\n"
        "  - id: proj:beta.md\n"
        "    project: proj\n"
        "    path: beta.md\n"
        "    kind: pattern\n"
        "    tags: [second, common]\n"
        "  - id: proj:gamma.md\n"
        "    project: proj\n"
        "    path: gamma.md\n"
        "    kind: task\n"
        "    tags: [third]\n"
    )
    (store_dir / "annotations-config.yaml").write_text(annotations_yaml, encoding="utf-8")

    seed = runner.invoke(app, ["--store", str(store_dir), "reindex"])
    assert seed.exit_code == 0, seed.output
    return store_dir, project_dir


def test_list_returns_all_documents_in_json(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "list", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 3
    assert {entry["id"] for entry in parsed} == {
        "proj:alpha.md",
        "proj:beta.md",
        "proj:gamma.md",
    }
    for entry in parsed:
        assert "body" not in entry


def test_list_filters_compose(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "list",
            "--kind",
            "doc",
            "--project",
            "proj",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert len(parsed) == 1
    assert parsed[0]["id"] == "proj:alpha.md"
    assert parsed[0]["kind"] == "doc"


def test_list_table_renders_columns(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "list", "--format", "table"])

    assert result.exit_code == 0, result.output
    header = result.output.splitlines()[0]
    for column in ("id", "kind", "project", "title", "tags"):
        assert column in header


def test_query_matches_known_token(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    matched = runner.invoke(
        app, ["--store", str(store_dir), "query", "uniqalpha", "--format", "json"]
    )
    assert matched.exit_code == 0, matched.output
    parsed = json.loads(matched.output)
    assert len(parsed) >= 1
    assert {entry["document_id"] for entry in parsed} == {"proj:alpha.md"}
    assert all(entry["chunk_id"].startswith("proj:alpha.md#") for entry in parsed)

    capped = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "shared",
            "--limit",
            "1",
            "--format",
            "json",
        ],
    )
    assert capped.exit_code == 0, capped.output
    capped_parsed = json.loads(capped.output)
    assert len(capped_parsed) == 1

    missing = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "nothingmatchesthistoken",
            "--format",
            "json",
        ],
    )
    assert missing.exit_code == 0, missing.output
    assert json.loads(missing.output) == []


def test_query_json_has_chunk_shape(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app, ["--store", str(store_dir), "query", "uniqalpha", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert len(parsed) >= 1
    entry = parsed[0]
    assert set(entry.keys()) == {
        "chunk_id",
        "document_id",
        "kind",
        "title",
        "project",
        "heading_path",
        "slug",
        "summary",
        "snippet",
    }


def test_query_table_renders_chunk_columns(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app, ["--store", str(store_dir), "query", "uniqalpha", "--format", "table"]
    )

    assert result.exit_code == 0, result.output
    header = result.output.splitlines()[0]
    for column in ("kind", "chunk_id", "heading", "summary"):
        assert column in header


def test_get_prints_raw_markdown(tmp_path: Path) -> None:
    store_dir, project_dir = _make_seeded_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "get", "proj:alpha.md"])

    assert result.exit_code == 0, result.output
    expected = (project_dir / "alpha.md").read_text(encoding="utf-8")
    assert result.output.rstrip("\n") == expected.rstrip("\n")


def test_get_missing_id_exits_one(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "get", "proj:does-not-exist.md"])

    assert result.exit_code == 1
    assert "not found" in result.output


def test_show_missing_id_exits_one(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "show", "proj:does-not-exist.md", "--format", "json"],
    )

    assert result.exit_code == 1
    assert "not found" in result.output


def test_show_json_contains_body(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "show", "proj:alpha.md", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert "body" in parsed
    assert "uniqalpha" in parsed["body"]


def test_show_table_contains_body_section(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "show", "proj:alpha.md", "--format", "table"],
    )

    assert result.exit_code == 0, result.output
    assert "---" in result.output
    sep = result.output.index("---")
    assert "uniqalpha" in result.output[sep:]


def test_format_default_pipe_is_json(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "list"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 3


def test_resolve_format_returns_table_when_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert _resolve_format(None) == "table"


def test_resolve_format_returns_json_when_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert _resolve_format(None) == "json"


def test_resolve_format_explicit_flag_wins_over_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert _resolve_format("json") == "json"
    assert _resolve_format("table") == "table"


def test_list_filter_by_source_type(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "list", "--source-type", "project", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 3

    empty = runner.invoke(
        app,
        ["--store", str(store_dir), "list", "--source-type", "global", "--format", "json"],
    )
    assert empty.exit_code == 0, empty.output
    assert json.loads(empty.output) == []


def test_list_rejects_invalid_source_type(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "list", "--source-type", "bogus"],
    )

    assert result.exit_code != 0


def test_query_filter_by_kind(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "shared",
            "--kind",
            "pattern",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert len(parsed) >= 1
    assert {entry["document_id"] for entry in parsed} == {"proj:beta.md"}


def test_query_filter_by_project(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "query",
            "shared",
            "--project",
            "proj",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert {entry["document_id"] for entry in parsed} == {"proj:beta.md", "proj:gamma.md"}


def test_query_invalid_fts_returns_clean_error(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", '"unbalanced'],
    )

    assert result.exit_code == 1
    assert "error:" in result.output
    assert "Traceback" not in result.output


def test_query_rejects_non_positive_limit(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    for bad in ("0", "-1"):
        result = runner.invoke(
            app,
            ["--store", str(store_dir), "query", "uniqalpha", "--limit", bad],
        )
        assert result.exit_code == 1, result.output
        assert "--limit" in result.output


def test_get_rejects_format_flag(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "get", "proj:alpha.md", "--format", "json"],
    )

    assert result.exit_code != 0


def test_get_chunk_prints_chunk_body(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    list_result = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "uniqalpha", "--format", "json"],
    )
    assert list_result.exit_code == 0, list_result.output
    chunks = json.loads(list_result.output)
    assert len(chunks) >= 1
    chunk_id = chunks[0]["chunk_id"]

    result = runner.invoke(app, ["--store", str(store_dir), "get", chunk_id])

    assert result.exit_code == 0, result.output
    assert "uniqalpha" in result.output
    assert result.output.endswith("\n")


def test_get_chunk_unknown_exits_one(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "get", "proj:alpha.md#unknown"])

    assert result.exit_code == 1
    assert "unknown chunk" in result.output


def test_get_appends_trailing_newline_when_missing(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()

    _write(project_dir / "no-trailing.md", "# NoTrail\n\nbody-without-newline-at-end")

    config_yaml = f"projects:\n  - name: proj\n    path: {project_dir}\ndefaults:\n  kind: doc\n"
    (store_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")
    (store_dir / "annotations-config.yaml").write_text(
        "documents:\n"
        "  - id: proj:no-trailing.md\n"
        "    project: proj\n"
        "    path: no-trailing.md\n"
        "    kind: doc\n",
        encoding="utf-8",
    )
    seed = runner.invoke(app, ["--store", str(store_dir), "reindex"])
    assert seed.exit_code == 0, seed.output

    result = runner.invoke(app, ["--store", str(store_dir), "get", "proj:no-trailing.md"])

    assert result.exit_code == 0, result.output
    assert result.output.endswith("body-without-newline-at-end\n")
