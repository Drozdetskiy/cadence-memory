"""End-to-end tests for the `cadence-memory ephemeral` subcommand group."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from cadence_memory.cli import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_store(tmp_path: Path) -> Path:
    """Bootstrap a minimal store directory ready for ephemeral commands."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "ephemeral").mkdir()
    (store_dir / "config.yaml").write_text("projects: []\n", encoding="utf-8")
    (store_dir / "annotations-config.yaml").write_text("documents: []\n", encoding="utf-8")
    return store_dir


def _make_seeded_store(tmp_path: Path) -> tuple[Path, Path]:
    """Store with one project document already indexed; ephemeral dir present."""
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    (store_dir / "ephemeral").mkdir()
    project_dir.mkdir()

    _write(project_dir / "alpha.md", "# Alpha\n\nuniqalpha body\n")

    config_yaml = (
        f"projects:\n  - name: proj\n    path: {project_dir}\n"
        "defaults:\n  kind: doc\n"
        "enrichment:\n  enabled: false\n"
    )
    (store_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")

    annotations_yaml = (
        "documents:\n  - id: proj:alpha.md\n    project: proj\n    path: alpha.md\n    kind: doc\n"
    )
    (store_dir / "annotations-config.yaml").write_text(annotations_yaml, encoding="utf-8")

    seed = runner.invoke(app, ["--store", str(store_dir), "reindex"])
    assert seed.exit_code == 0, seed.output
    return store_dir, project_dir


def test_ephemeral_add_copy_happy_path(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nbody\n", encoding="utf-8")

    result = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])

    assert result.exit_code == 0, result.output
    assert "eph:note" in result.output
    assert (store_dir / "ephemeral" / "note.md").is_file()

    listing = runner.invoke(
        app, ["--store", str(store_dir), "ephemeral", "list", "--format", "json"]
    )
    assert listing.exit_code == 0, listing.output
    parsed = json.loads(listing.output)
    assert len(parsed) == 1
    assert parsed[0]["id"] == "eph:note"
    assert parsed[0]["source_type"] == "ephemeral"


def test_ephemeral_add_inline_with_id(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "ephemeral",
            "add",
            "--inline",
            "-",
            "--id",
            "mynote",
        ],
        input="# Inline\n\ninline body\n",
    )

    assert result.exit_code == 0, result.output
    assert "eph:mynote" in result.output
    target = store_dir / "ephemeral" / "mynote.md"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "# Inline\n\ninline body\n"

    show = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "list", "--format", "json"])
    parsed = json.loads(show.output)
    assert parsed[0]["id"] == "eph:mynote"


def test_ephemeral_add_inline_without_id_errors(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "ephemeral", "add", "--inline", "-"],
        input="# X\n\nbody\n",
    )

    assert result.exit_code == 1, result.output
    assert "--id" in result.output


def test_ephemeral_add_duplicate_errors(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "dup.md"
    src.write_text("# Dup\n\nbody\n", encoding="utf-8")

    first = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])
    assert second.exit_code == 1, second.output
    assert "already" in second.output


def test_ephemeral_add_symlink_keeps_source_after_remove(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "linked.md"
    src.write_text("# Linked\n\nlinked body\n", encoding="utf-8")

    add_res = runner.invoke(
        app,
        ["--store", str(store_dir), "ephemeral", "add", str(src), "--symlink"],
    )
    assert add_res.exit_code == 0, add_res.output

    target = store_dir / "ephemeral" / "linked.md"
    assert target.is_symlink()

    rm_res = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "remove", "eph:linked"])
    assert rm_res.exit_code == 0, rm_res.output
    assert "removed: eph:linked" in rm_res.output

    assert not target.exists()
    assert src.is_file()
    assert src.read_text(encoding="utf-8") == "# Linked\n\nlinked body\n"


def test_ephemeral_remove_then_list_empty(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "rm.md"
    src.write_text("# Rm\n\nbody\n", encoding="utf-8")

    runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])
    rm_res = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "remove", "eph:rm"])
    assert rm_res.exit_code == 0, rm_res.output

    listing = runner.invoke(
        app, ["--store", str(store_dir), "ephemeral", "list", "--format", "json"]
    )
    assert listing.exit_code == 0, listing.output
    assert json.loads(listing.output) == []


def test_ephemeral_clear_decline(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "k.md"
    src.write_text("# K\n\nbody\n", encoding="utf-8")
    runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])

    result = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "clear"], input="n\n")

    assert result.exit_code == 1, result.output
    assert "aborted" in result.output

    listing = runner.invoke(
        app, ["--store", str(store_dir), "ephemeral", "list", "--format", "json"]
    )
    assert len(json.loads(listing.output)) == 1


def test_ephemeral_clear_confirm(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    for stem in ("a", "b"):
        src = tmp_path / f"{stem}.md"
        src.write_text(f"# {stem}\n\nbody\n", encoding="utf-8")
        runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])

    result = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "clear"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "cleared: 2 document(s)" in result.output

    listing = runner.invoke(
        app, ["--store", str(store_dir), "ephemeral", "list", "--format", "json"]
    )
    assert json.loads(listing.output) == []


def test_ephemeral_clear_yes_skips_prompt(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "y.md"
    src.write_text("# Y\n\nbody\n", encoding="utf-8")
    runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])

    result = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared: 1 document(s)" in result.output


def test_top_level_list_includes_ephemeral(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)
    src = tmp_path / "e.md"
    src.write_text("# E\n\nbody\n", encoding="utf-8")
    add_res = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])
    assert add_res.exit_code == 0, add_res.output

    listing = runner.invoke(app, ["--store", str(store_dir), "list", "--format", "json"])
    assert listing.exit_code == 0, listing.output
    parsed = json.loads(listing.output)
    ids = {entry["id"] for entry in parsed}
    assert "eph:e" in ids
    assert "proj:alpha.md" in ids


def test_ephemeral_list_excludes_project_rows(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)
    src = tmp_path / "only.md"
    src.write_text("# Only\n\nbody\n", encoding="utf-8")
    add_res = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add", str(src)])
    assert add_res.exit_code == 0, add_res.output

    listing = runner.invoke(
        app, ["--store", str(store_dir), "ephemeral", "list", "--format", "json"]
    )
    assert listing.exit_code == 0, listing.output
    parsed = json.loads(listing.output)
    assert {entry["id"] for entry in parsed} == {"eph:only"}


def test_ephemeral_add_inline_invalid_value(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "ephemeral",
            "add",
            "--inline",
            "literal",
            "--id",
            "x",
        ],
    )

    assert result.exit_code != 0
    assert "--inline" in _strip_ansi(result.output)


def test_ephemeral_add_path_with_inline_errors(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "x.md"
    src.write_text("# X\n\nbody\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "ephemeral",
            "add",
            str(src),
            "--inline",
            "-",
            "--id",
            "x",
        ],
        input="# Y\n\ny\n",
    )

    assert result.exit_code == 1, result.output
    assert "cannot combine --inline" in result.output


def test_ephemeral_add_no_path_no_inline_errors(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "add"])

    assert result.exit_code == 1, result.output
    assert "source file argument is required" in result.output


def test_ephemeral_add_invalid_id_errors(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "x.md"
    src.write_text("# X\n\nbody\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["--store", str(store_dir), "ephemeral", "add", str(src), "--id", "bad/name"],
    )

    assert result.exit_code == 1, result.output
    assert "ephemeral name" in result.output


def test_ephemeral_remove_rejects_non_ephemeral_id(tmp_path: Path) -> None:
    store_dir, _ = _make_seeded_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "ephemeral", "remove", "proj:alpha.md"])

    assert result.exit_code == 1, result.output
    assert "not an ephemeral document id" in result.output

    listing = runner.invoke(app, ["--store", str(store_dir), "list", "--format", "json"])
    parsed = json.loads(listing.output)
    assert {entry["id"] for entry in parsed} == {"proj:alpha.md"}


def test_ephemeral_add_with_tags(tmp_path: Path) -> None:
    store_dir = _make_store(tmp_path)
    src = tmp_path / "t.md"
    src.write_text("# T\n\nbody\n", encoding="utf-8")

    add_res = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "ephemeral",
            "add",
            str(src),
            "--tags",
            "alpha,, beta ,",
        ],
    )
    assert add_res.exit_code == 0, add_res.output

    listing = runner.invoke(
        app, ["--store", str(store_dir), "ephemeral", "list", "--format", "json"]
    )
    parsed = json.loads(listing.output)
    assert parsed[0]["tags"] == ["alpha", "beta"]
