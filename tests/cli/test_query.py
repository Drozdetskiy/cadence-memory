"""Tests for the `cadence-memory query` CLI command (design2 §10, §11)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.search import Hit, NoBackendAvailableError, SearchError
from cadence_memory.wiki import scaffold_wiki


def _scaffold(tmp_path: Path) -> Path:
    scaffold_wiki(tmp_path)
    return tmp_path


@dataclass
class _SearchCall:
    kwargs: dict[str, Any]


class _FakeBackend:
    name = "qmd"

    def __init__(
        self,
        hits: tuple[Hit, ...],
        *,
        error: SearchError | None = None,
        record: list[_SearchCall] | None = None,
    ) -> None:
        self._hits = hits
        self._error = error
        self._record = record

    def search(self, *, query: str, wiki_dir: Path, limit: int) -> tuple[Hit, ...]:
        if self._record is not None:
            self._record.append(
                _SearchCall(kwargs={"query": query, "wiki_dir": wiki_dir, "limit": limit})
            )
        if self._error is not None:
            raise self._error
        return self._hits


def _install_pick_backend(monkeypatch: pytest.MonkeyPatch, backend: _FakeBackend) -> None:
    monkeypatch.setattr("cadence_memory.cli.pick_backend", lambda: backend)


def test_cli_query_table_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    hits = (
        Hit(
            path=wiki / "projects" / "alpha.md",
            score=0.91,
            snippet="alpha mentions billing",
            backend="qmd",
        ),
        Hit(
            path=wiki / "learnings.md",
            score=0.42,
            snippet="billing flow notes",
            backend="qmd",
        ),
    )
    _install_pick_backend(monkeypatch, _FakeBackend(hits))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "billing", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "SCORE" in result.stdout
    assert "PATH" in result.stdout
    assert "SNIPPET" in result.stdout
    assert "0.91" in result.stdout
    assert "0.42" in result.stdout
    assert "projects/alpha.md" in result.stdout
    assert "learnings.md" in result.stdout
    assert str(wiki) not in result.stdout.split("\n", 1)[1]  # paths are relative


def test_cli_query_ripgrep_dash_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    hits = (
        Hit(
            path=wiki / "learnings.md",
            score=None,
            snippet="rg snippet here",
            backend="ripgrep",
        ),
    )
    _install_pick_backend(monkeypatch, _FakeBackend(hits))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "—" in result.stdout


def test_cli_query_json_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    hits = (
        Hit(
            path=wiki / "projects" / "alpha.md",
            score=0.91,
            snippet="alpha",
            backend="qmd",
        ),
        Hit(
            path=wiki / "learnings.md",
            score=None,
            snippet="lessons",
            backend="ripgrep",
        ),
    )
    _install_pick_backend(monkeypatch, _FakeBackend(hits))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--format", "json", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    for entry in parsed:
        assert set(entry.keys()) == {"path", "score", "snippet", "backend"}
        assert Path(entry["path"]).is_absolute()
    assert parsed[0]["score"] == 0.91
    assert parsed[1]["score"] is None
    assert parsed[0]["backend"] == "qmd"
    assert parsed[1]["backend"] == "ripgrep"


def test_cli_query_empty_results_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    _install_pick_backend(monkeypatch, _FakeBackend(()))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "nothing", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "no matches" in result.stdout


def test_cli_query_empty_results_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    _install_pick_backend(monkeypatch, _FakeBackend(()))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "nothing", "--format", "json", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "[]"


def test_cli_query_forced_qmd_unavailable_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    monkeypatch.setattr("cadence_memory.cli.QmdBackend.available", staticmethod(lambda: False))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--backend", "qmd", "--wiki", str(wiki)])

    assert result.exit_code == 2
    assert "qmd not on $PATH" in result.stderr
    assert "brew install qmd" in result.stderr


def test_cli_query_forced_ripgrep_unavailable_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    monkeypatch.setattr("cadence_memory.cli.RipgrepBackend.available", staticmethod(lambda: False))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--backend", "ripgrep", "--wiki", str(wiki)])

    assert result.exit_code == 2
    assert "ripgrep not on $PATH" in result.stderr


def test_cli_query_neither_backend_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)

    def fake_pick() -> Any:
        raise NoBackendAvailableError("neither qmd nor ripgrep on $PATH — install one")

    monkeypatch.setattr("cadence_memory.cli.pick_backend", fake_pick)
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--wiki", str(wiki)])

    assert result.exit_code == 2
    assert "qmd" in result.stderr
    assert "ripgrep" in result.stderr


def test_cli_query_invalid_format_exits_2(tmp_path: Path) -> None:
    wiki = _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--format", "xml", "--wiki", str(wiki)])

    assert result.exit_code == 2
    assert "--format" in result.stderr


def test_cli_query_invalid_backend_exits_2(tmp_path: Path) -> None:
    wiki = _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--backend", "wat", "--wiki", str(wiki)])

    assert result.exit_code == 2
    assert "--backend" in result.stderr


def test_cli_query_search_error_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    _install_pick_backend(monkeypatch, _FakeBackend((), error=SearchError("boom")))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--wiki", str(wiki)])

    assert result.exit_code == 1
    assert "search failed" in result.stderr
    assert "boom" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_query_wiki_not_found_exits_1(tmp_path: Path) -> None:
    nowhere = tmp_path / "no-wiki-here"
    nowhere.mkdir()
    runner = CliRunner()

    result = runner.invoke(app, ["query", "anything", "--wiki", str(nowhere)])

    assert result.exit_code == 1
    assert "config.yaml" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_query_limit_passed_to_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    captured: list[_SearchCall] = []
    _install_pick_backend(monkeypatch, _FakeBackend((), record=captured))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "billing", "-n", "5", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(captured) == 1
    assert captured[0].kwargs["limit"] == 5
    assert captured[0].kwargs["query"] == "billing"
    assert captured[0].kwargs["wiki_dir"] == wiki


def test_cli_query_auto_backend_literal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    hits = (
        Hit(
            path=wiki / "page.md",
            score=0.5,
            snippet="hit",
            backend="qmd",
        ),
    )
    _install_pick_backend(monkeypatch, _FakeBackend(hits))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "x", "--backend", "auto", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "hit" in result.stdout


def test_cli_query_forced_qmd_returns_qmd_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    captured: list[_SearchCall] = []
    monkeypatch.setattr("cadence_memory.cli.QmdBackend.available", staticmethod(lambda: True))
    monkeypatch.setattr("cadence_memory.cli.RipgrepBackend.available", staticmethod(lambda: True))

    def fake_qmd_search(self: object, *, query: str, wiki_dir: Path, limit: int) -> tuple[Hit, ...]:
        captured.append(_SearchCall(kwargs={"backend": "qmd"}))
        return ()

    def fake_rg_search(self: object, *, query: str, wiki_dir: Path, limit: int) -> tuple[Hit, ...]:
        captured.append(_SearchCall(kwargs={"backend": "ripgrep"}))
        return ()

    monkeypatch.setattr("cadence_memory.cli.QmdBackend.search", fake_qmd_search)
    monkeypatch.setattr("cadence_memory.cli.RipgrepBackend.search", fake_rg_search)
    runner = CliRunner()

    result = runner.invoke(app, ["query", "x", "--backend", "qmd", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert captured == [_SearchCall(kwargs={"backend": "qmd"})]


def test_cli_query_forced_ripgrep_returns_ripgrep_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    captured: list[_SearchCall] = []
    monkeypatch.setattr("cadence_memory.cli.QmdBackend.available", staticmethod(lambda: True))
    monkeypatch.setattr("cadence_memory.cli.RipgrepBackend.available", staticmethod(lambda: True))

    def fake_qmd_search(self: object, *, query: str, wiki_dir: Path, limit: int) -> tuple[Hit, ...]:
        captured.append(_SearchCall(kwargs={"backend": "qmd"}))
        return ()

    def fake_rg_search(self: object, *, query: str, wiki_dir: Path, limit: int) -> tuple[Hit, ...]:
        captured.append(_SearchCall(kwargs={"backend": "ripgrep"}))
        return ()

    monkeypatch.setattr("cadence_memory.cli.QmdBackend.search", fake_qmd_search)
    monkeypatch.setattr("cadence_memory.cli.RipgrepBackend.search", fake_rg_search)
    runner = CliRunner()

    result = runner.invoke(app, ["query", "x", "--backend", "ripgrep", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert captured == [_SearchCall(kwargs={"backend": "ripgrep"})]


def test_cli_query_table_falls_back_to_absolute_path_outside_wiki(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    outside = tmp_path.parent / "elsewhere.md"
    hits = (
        Hit(
            path=outside.resolve(),
            score=0.5,
            snippet="external hit",
            backend="qmd",
        ),
    )
    _install_pick_backend(monkeypatch, _FakeBackend(hits))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "x", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert str(outside.resolve()) in result.stdout


def test_cli_query_table_sanitizes_snippet_newlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    hits = (
        Hit(
            path=wiki / "page.md",
            score=0.5,
            snippet="first line\nsecond line",
            backend="qmd",
        ),
    )
    _install_pick_backend(monkeypatch, _FakeBackend(hits))
    runner = CliRunner()

    result = runner.invoke(app, ["query", "x", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.stdout + result.stderr
    body_lines = [line for line in result.stdout.splitlines() if "first line" in line]
    assert len(body_lines) == 1
    assert "first line second line" in body_lines[0]


def test_cli_query_long_snippet_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    long_snippet = "x" * 200
    hits = (
        Hit(
            path=wiki / "learnings.md",
            score=0.5,
            snippet=long_snippet,
            backend="qmd",
        ),
    )
    _install_pick_backend(monkeypatch, _FakeBackend(hits))
    runner = CliRunner()

    table_result = runner.invoke(app, ["query", "anything", "--wiki", str(wiki)])
    assert table_result.exit_code == 0
    assert "…" in table_result.stdout
    assert long_snippet not in table_result.stdout

    json_result = runner.invoke(app, ["query", "anything", "--format", "json", "--wiki", str(wiki)])
    assert json_result.exit_code == 0
    parsed = json.loads(json_result.stdout)
    assert parsed[0]["snippet"] == long_snippet
