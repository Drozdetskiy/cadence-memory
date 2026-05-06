"""End-to-end tests for the `backlinks` and `mentions` CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cadence_memory.cli import app

runner = CliRunner()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_seeded_store(tmp_path: Path) -> tuple[Path, Path]:
    """Seed a store with documents that contain a mix of mentionable targets."""
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()

    _write(
        project_dir / "alpha.md",
        (
            "# Alpha\n"
            "\n"
            "see src/foo.py:100-200 for details, also POST /v1/customers\n"
            "and the schema `BillingEntity` defined in [other](other.md).\n"
        ),
    )
    _write(
        project_dir / "beta.md",
        "# Beta\n\nalso see src/foo.py:42 and `BillingEntity` again.\n",
    )
    _write(
        project_dir / "gamma.md",
        "# Gamma\n\nno interesting mentions here, just words.\n",
    )

    config_yaml = (
        f"projects:\n  - name: proj\n    path: {project_dir}\n"
        "defaults:\n  kind: doc\n"
        "enrichment:\n  enabled: false\n"
        "query:\n  expansion:\n    enabled: false\n"
    )
    (store_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")

    annotations_yaml = (
        "documents:\n"
        "  - id: proj:alpha.md\n"
        "    project: proj\n"
        "    path: alpha.md\n"
        "    kind: doc\n"
        "  - id: proj:beta.md\n"
        "    project: proj\n"
        "    path: beta.md\n"
        "    kind: pattern\n"
        "  - id: proj:gamma.md\n"
        "    project: proj\n"
        "    path: gamma.md\n"
        "    kind: doc\n"
    )
    (store_dir / "annotations-config.yaml").write_text(annotations_yaml, encoding="utf-8")

    seed = runner.invoke(app, ["--store", str(store_dir), "reindex"])
    assert seed.exit_code == 0, seed.output
    return store_dir, project_dir


def test_backlinks_no_match_prints_no_backlinks(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "backlinks", "nothing/nowhere.py", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert "(no backlinks)" in result.stderr
    assert result.stdout.strip() == ""


def test_backlinks_table_lists_chunks_per_line_range(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "backlinks", "src/foo.py", "--format", "table"],
    )

    assert result.exit_code == 0, result.output
    header = result.stdout.splitlines()[0]
    for column in ("chunk_id", "kind", "title", "line_range"):
        assert column in header
    assert "100-200" in result.stdout
    assert "42" in result.stdout
    assert "proj:alpha.md#" in result.stdout
    assert "proj:beta.md#" in result.stdout


def test_backlinks_json_payload_shape(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "backlinks", "src/foo.py", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) >= 2
    for entry in parsed:
        assert set(entry.keys()) == {
            "chunk_id",
            "document_id",
            "kind",
            "title",
            "line_range",
        }
    line_ranges = sorted(entry["line_range"] for entry in parsed)
    assert "100-200" in line_ranges
    assert "42" in line_ranges


def test_backlinks_filter_by_kind(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    schema_result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "backlinks",
            "BillingEntity",
            "--kind",
            "schema",
            "--format",
            "json",
        ],
    )
    assert schema_result.exit_code == 0, schema_result.output
    schema_parsed = json.loads(schema_result.stdout)
    assert len(schema_parsed) >= 1
    assert all(
        entry["chunk_id"].startswith(("proj:alpha.md#", "proj:beta.md#")) for entry in schema_parsed
    )

    code_result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "backlinks",
            "BillingEntity",
            "--kind",
            "code",
            "--format",
            "json",
        ],
    )
    assert code_result.exit_code == 0, code_result.output
    assert code_result.stdout.strip() == ""
    assert "(no backlinks)" in code_result.stderr


def test_backlinks_help_smoke(tmp_path: Path) -> None:
    result = runner.invoke(app, ["backlinks", "--help"])
    assert result.exit_code == 0, result.output
    assert "backlinks" in result.output.lower()


def _alpha_chunk_id(store_dir: Path) -> str:
    listed = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "BillingEntity", "--format", "json"],
    )
    assert listed.exit_code == 0, listed.output
    chunks = json.loads(listed.stdout)
    for entry in chunks:
        if entry["document_id"] == "proj:alpha.md":
            chunk_id: str = entry["chunk_id"]
            return chunk_id
    raise AssertionError("no alpha chunk found")


def test_mentions_table_lists_targets(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)
    chunk_id = _alpha_chunk_id(store_dir)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "mentions", chunk_id, "--format", "table"],
    )

    assert result.exit_code == 0, result.output
    header = result.stdout.splitlines()[0]
    for column in ("kind", "target", "line_range"):
        assert column in header
    assert "src/foo.py" in result.stdout
    assert "POST /v1/customers" in result.stdout
    assert "BillingEntity" in result.stdout
    assert "other.md" in result.stdout


def test_mentions_json_payload_shape(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)
    chunk_id = _alpha_chunk_id(store_dir)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "mentions", chunk_id, "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) >= 4
    for entry in parsed:
        assert set(entry.keys()) == {"target_kind", "target", "line_range"}
    targets = {entry["target"] for entry in parsed}
    assert {"src/foo.py", "POST /v1/customers", "BillingEntity", "other.md"} <= targets


def test_mentions_unknown_chunk_exits_one(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "mentions", "proj:alpha.md#nonsense"],
    )

    assert result.exit_code == 1
    assert "unknown chunk" in result.stderr


def test_mentions_chunk_with_no_mentions_prints_no_mentions(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)
    listed = runner.invoke(
        app,
        ["--store", str(store_dir), "query", "interesting", "--format", "json"],
    )
    assert listed.exit_code == 0, listed.output
    chunks = json.loads(listed.stdout)
    gamma = next(entry for entry in chunks if entry["document_id"] == "proj:gamma.md")
    chunk_id = gamma["chunk_id"]

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "mentions", chunk_id, "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert "(no mentions)" in result.stderr
    assert result.stdout.strip() == ""


def test_mentions_help_smoke(tmp_path: Path) -> None:
    result = runner.invoke(app, ["mentions", "--help"])
    assert result.exit_code == 0, result.output
    assert "mentions" in result.output.lower()
