"""Tests for the `cadence-memory ingest` CLI command (design2 §11)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.wiki import scaffold_wiki
from cadence_memory.worker.manual import ManualIngestOutcome

_OK_SHA = "abc1234"


def _scaffold(tmp_path: Path) -> Path:
    scaffold_wiki(tmp_path)
    return tmp_path


def _scaffold_with_color(tmp_path: Path) -> Path:
    wiki = _scaffold(tmp_path)
    config_path = wiki / "config.yaml"
    config_path.write_text(config_path.read_text() + "progress:\n  color: always\n")
    return wiki


def _write_source(wiki: Path, name: str = "article.md") -> Path:
    raw_dir = wiki / "raw" / "notes"
    raw_dir.mkdir(parents=True, exist_ok=True)
    src = raw_dir / name
    src.write_text("plain article content\n", encoding="utf-8")
    return src


@dataclass
class _IngestCall:
    kwargs: dict[str, Any]


def _fake_ingest_file_factory(
    *,
    outcome: ManualIngestOutcome,
    record: list[_IngestCall] | None = None,
) -> Any:
    def fake_ingest_file(**kwargs: Any) -> ManualIngestOutcome:
        if record is not None:
            record.append(_IngestCall(kwargs=kwargs))
        return outcome

    return fake_ingest_file


def _success_outcome(
    source: Path,
    *,
    cost_usd: float | None = 0.42,
    pages: tuple[Path, ...] = (),
    sha: str = _OK_SHA,
) -> ManualIngestOutcome:
    return ManualIngestOutcome(
        success=True,
        source_path=source,
        pages_touched=pages,
        wiki_commit_sha=sha,
        cost_usd=cost_usd,
        error=None,
    )


def _failure_outcome(source: Path, *, error: str = "boom") -> ManualIngestOutcome:
    return ManualIngestOutcome(
        success=False,
        source_path=source,
        pages_touched=(),
        wiki_commit_sha=None,
        cost_usd=0.01,
        error=error,
    )


def test_cli_ingest_exit_0_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki)
    outcome = _success_outcome(
        src,
        cost_usd=0.42,
        pages=(wiki / "learnings.md",),
    )
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file",
        _fake_ingest_file_factory(outcome=outcome),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(src), "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "ok:" in result.stdout
    assert _OK_SHA in result.stdout
    assert "1 pages" in result.stdout
    assert "$0.42" in result.stdout


def test_cli_ingest_exit_1_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki)
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file",
        _fake_ingest_file_factory(outcome=_failure_outcome(src, error="claude went sideways")),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(src), "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "failed:" in result.stderr
    assert "claude went sideways" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_ingest_exit_1_on_failure_with_none_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki)
    outcome = ManualIngestOutcome(
        success=False,
        source_path=src,
        pages_touched=(),
        wiki_commit_sha=None,
        cost_usd=None,
        error=None,
    )
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file",
        _fake_ingest_file_factory(outcome=outcome),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(src), "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "failed:" in result.stderr
    assert "None" not in result.stderr
    assert "claude run failed" in result.stderr


def test_cli_ingest_exit_2_on_missing_source(tmp_path: Path) -> None:
    wiki = _scaffold(tmp_path)
    missing = wiki / "raw" / "notes" / "does-not-exist.md"
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(missing), "--wiki", str(wiki)])

    assert result.exit_code == 2
    assert "error: source file not found" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_ingest_warns_outside_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path / "wiki")
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    outside_src = outside_dir / "scratch.md"
    outside_src.write_text("hello\n", encoding="utf-8")

    captured: list[_IngestCall] = []
    outcome = _success_outcome(outside_src, pages=())
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file",
        _fake_ingest_file_factory(outcome=outcome, record=captured),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(outside_src), "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "warning: source is outside wiki" in result.stderr
    assert len(captured) == 1


def test_cli_ingest_relative_path_resolved_against_wiki(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki, name="x.md")
    expected = (wiki / "raw" / "notes" / "x.md").resolve()

    captured: list[_IngestCall] = []
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file",
        _fake_ingest_file_factory(outcome=_success_outcome(src), record=captured),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["ingest", "raw/notes/x.md", "--wiki", str(wiki)],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    assert captured[0].kwargs["source_path"] == expected


def test_cli_ingest_cost_none_renders_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki)
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file",
        _fake_ingest_file_factory(outcome=_success_outcome(src, cost_usd=None)),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(src), "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "ok:" in result.stdout
    assert "pages" in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_ingest_no_changes_renders_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki)
    outcome = ManualIngestOutcome(
        success=True,
        source_path=src,
        pages_touched=(),
        wiki_commit_sha=None,
        cost_usd=0.01,
        error=None,
    )
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file",
        _fake_ingest_file_factory(outcome=outcome),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(src), "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "ok:" in result.stdout
    assert "None" not in result.stdout
    assert "no changes" in result.stdout


def test_cli_ingest_exit_1_on_wiki_not_found(tmp_path: Path) -> None:
    nowhere = tmp_path / "no-wiki-here"
    nowhere.mkdir()
    src = tmp_path / "scratch.md"
    src.write_text("hello\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(src), "--wiki", str(nowhere)])

    assert result.exit_code == 1
    assert "config.yaml" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_ingest_exit_1_on_bad_config(tmp_path: Path) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki)
    (wiki / "config.yaml").write_text(": invalid: yaml: [[[\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(src), "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "invalid YAML" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_ingest_phase_header_and_summary_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki)
    outcome = _success_outcome(src, cost_usd=0.07, pages=(wiki / "learnings.md",))
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file", _fake_ingest_file_factory(outcome=outcome)
    )
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", str(src), "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    # (a) phase header
    assert "ingesting" in result.stdout
    # (b) final summary present
    assert "ok:" in result.stdout
    assert "$0.07" in result.stdout


def test_cli_ingest_no_color_strips_ansi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold_with_color(tmp_path)
    src = _write_source(wiki)
    outcome = _success_outcome(src)
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file", _fake_ingest_file_factory(outcome=outcome)
    )
    runner = CliRunner()

    result_color = runner.invoke(app, ["ingest", str(src), "--wiki", str(wiki)])
    assert "\x1b[" in result_color.stdout

    result_plain = runner.invoke(app, ["--no-color", "ingest", str(src), "--wiki", str(wiki)])
    assert "\x1b[" not in result_plain.stdout
    assert "ok:" in result_plain.stdout


def test_cli_ingest_quiet_suppresses_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    src = _write_source(wiki)
    outcome = _success_outcome(src)
    monkeypatch.setattr(
        "cadence_memory.cli.ingest_file", _fake_ingest_file_factory(outcome=outcome)
    )
    runner = CliRunner()

    result_quiet = runner.invoke(app, ["--quiet", "ingest", str(src), "--wiki", str(wiki)])
    assert result_quiet.exit_code == 0, result_quiet.stdout + result_quiet.stderr
    assert "ingesting" not in result_quiet.stdout
    assert "ok:" in result_quiet.stdout
