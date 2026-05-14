"""Tests for the `cadence-memory status` CLI command (design2 §11)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cadence_memory.cli import app
from cadence_memory.git.cache import CloneResult, CommitInfo
from cadence_memory.git.errors import GitError, HistoryRewrittenError
from cadence_memory.wiki import scaffold_wiki
from cadence_memory.worker.state import RepoState, WorkerState, save_state


def _scaffold(tmp_path: Path) -> Path:
    scaffold_wiki(tmp_path)
    return tmp_path


def _write_config(wiki: Path, repos: list[tuple[str, str]]) -> None:
    lines = ["repos:"]
    for name, branch in repos:
        lines.extend(
            [
                f"  - name: {name}",
                f"    url: https://example.com/{name}.git",
                f"    branch: {branch}",
            ]
        )
    if not repos:
        lines = ["repos: []"]
    (wiki / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_state(wiki: Path, repos: dict[str, RepoState]) -> None:
    state = WorkerState(repos=dict(repos))
    save_state(wiki / ".cadence-memory" / "state.json", state)


def _commit(sha: str) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject="test commit",
        body="",
        author_date_iso="2026-05-12T12:00:00+00:00",
        parents=(),
    )


@dataclass
class _FakeGitCache:
    head_local_results: dict[str, str | None] = field(default_factory=dict)
    list_commits_counts: dict[str, int] = field(default_factory=dict)
    list_commits_raises: set[str] = field(default_factory=set)
    list_commits_raises_giterror: set[str] = field(default_factory=set)
    raise_on_ensure: bool = False

    def ensure(self, *, name: str, url: str, branch: str) -> CloneResult:
        if self.raise_on_ensure:
            raise AssertionError(f"ensure() must not be called from status command (got {name!r})")
        raise NotImplementedError  # pragma: no cover

    def head(self, *, name: str, branch: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def head_local(self, *, name: str, branch: str) -> str | None:
        return self.head_local_results.get(name)

    def show_commit(self, *, name: str, sha: str) -> CommitInfo:  # pragma: no cover - unused
        raise NotImplementedError

    def diff(self, *, name: str, sha: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def changed_files(  # pragma: no cover - unused
        self, *, name: str, sha: str
    ) -> tuple[str, ...]:
        raise NotImplementedError

    def list_commits(
        self,
        *,
        name: str,
        since_sha: str | None,
        branch: str,
        reverse: bool = True,
    ) -> tuple[CommitInfo, ...]:
        if name in self.list_commits_raises:
            raise HistoryRewrittenError(f"history rewritten in {name}")
        if name in self.list_commits_raises_giterror:
            raise GitError(f"git log failed in {name}")
        count = self.list_commits_counts.get(name, 0)
        return tuple(_commit(f"sha{i:040d}") for i in range(count))


def _install_fake_cache(monkeypatch: pytest.MonkeyPatch, fake: _FakeGitCache) -> None:
    monkeypatch.setattr(
        "cadence_memory.cli_commands.status.DefaultGitCache",
        lambda *, root: fake,
    )


def test_status_table_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main"), ("project-b", "main")])
    fake = _FakeGitCache(
        head_local_results={"project-a": "a" * 40, "project-b": "b" * 40},
        list_commits_counts={"project-a": 3, "project-b": 0},
    )
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    assert "REPO" in result.stdout
    assert "BRANCH" in result.stdout
    assert "LAST_SHA" in result.stdout
    assert "PENDING" in result.stdout
    assert "LAST_RUN" in result.stdout
    assert "FAILURE" in result.stdout
    assert "project-a" in result.stdout
    assert "project-b" in result.stdout


def test_status_short_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main"), ("project-b", "main")])
    fake = _FakeGitCache(
        head_local_results={"project-a": "a" * 40, "project-b": "b" * 40},
        list_commits_counts={"project-a": 5, "project-b": 2},
    )
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--short", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 1
    assert lines[0].startswith("cadence-memory:")
    assert "2 repos" in lines[0]
    assert "7 pending commits" in lines[0]
    assert "0 recent failures" in lines[0]


def test_status_short_format_no_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    fake = _FakeGitCache()
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--short", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    assert "0 repos configured" in result.stdout
    assert "repos add" in result.stdout


def test_status_json_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main"), ("project-b", "develop")])
    fake = _FakeGitCache(
        head_local_results={"project-a": "a" * 40, "project-b": "b" * 40},
        list_commits_counts={"project-a": 1, "project-b": 0},
    )
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--format", "json", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert "wiki" in parsed
    assert "repos" in parsed
    assert len(parsed["repos"]) == 2
    for repo in parsed["repos"]:
        assert set(repo.keys()) == {
            "name",
            "branch",
            "last_sha",
            "pending",
            "last_run_at",
            "commits_processed",
            "last_failure",
        }


def test_status_uncloned_repo_shows_question_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main"), ("project-b", "main")])
    fake = _FakeGitCache(
        head_local_results={"project-a": "a" * 40, "project-b": None},
        list_commits_counts={"project-a": 2},
    )
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    table_result = runner.invoke(app, ["status", "--wiki", str(wiki)])
    assert table_result.exit_code == 0, table_result.output
    assert "?" in table_result.stdout

    json_result = runner.invoke(app, ["status", "--format", "json", "--wiki", str(wiki)])
    assert json_result.exit_code == 0, json_result.output
    parsed = json.loads(json_result.stdout)
    b_repo = next(r for r in parsed["repos"] if r["name"] == "project-b")
    assert b_repo["pending"] is None


def test_status_history_rewritten_shows_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main")])
    fake = _FakeGitCache(
        head_local_results={"project-a": "a" * 40},
        list_commits_raises={"project-a"},
    )
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    table_result = runner.invoke(app, ["status", "--wiki", str(wiki)])
    assert table_result.exit_code == 0, table_result.output
    assert "!hist" in table_result.stdout

    json_result = runner.invoke(app, ["status", "--format", "json", "--wiki", str(wiki)])
    assert json_result.exit_code == 0, json_result.output
    parsed = json.loads(json_result.stdout)
    assert parsed["repos"][0]["pending"] == -1

    short_result = runner.invoke(app, ["status", "--short", "--wiki", str(wiki)])
    assert short_result.exit_code == 0, short_result.output
    assert "history rewritten" in short_result.stdout


def test_status_git_error_does_not_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main")])
    fake = _FakeGitCache(
        head_local_results={"project-a": "a" * 40},
        list_commits_raises_giterror={"project-a"},
    )
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    table_result = runner.invoke(app, ["status", "--wiki", str(wiki)])
    assert table_result.exit_code == 0, table_result.output
    assert "Traceback" not in table_result.stdout
    assert "Traceback" not in table_result.stderr
    assert "!git" in table_result.stdout
    assert "!hist" not in table_result.stdout

    json_result = runner.invoke(app, ["status", "--format", "json", "--wiki", str(wiki)])
    assert json_result.exit_code == 0, json_result.output
    parsed = json.loads(json_result.stdout)
    assert parsed["repos"][0]["pending"] == -2

    short_result = runner.invoke(app, ["status", "--short", "--wiki", str(wiki)])
    assert short_result.exit_code == 0, short_result.output
    assert "git error" in short_result.stdout
    assert "history rewritten" not in short_result.stdout


def test_status_failure_shown_in_short_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main")])
    _write_state(wiki, {"project-a": RepoState(last_failure="Claude API timeout after 30s")})
    fake = _FakeGitCache(head_local_results={"project-a": "a" * 40})
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--short", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    assert "project-a" in result.stdout
    assert "Claude API" in result.stdout


def test_status_does_not_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main")])
    fake = _FakeGitCache(
        head_local_results={"project-a": "a" * 40},
        raise_on_ensure=True,
    )
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    default_result = runner.invoke(app, ["status", "--wiki", str(wiki)])
    assert default_result.exit_code == 0, default_result.output

    short_result = runner.invoke(app, ["status", "--short", "--wiki", str(wiki)])
    assert short_result.exit_code == 0, short_result.output


def test_status_invalid_format_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _scaffold(tmp_path)
    _install_fake_cache(monkeypatch, _FakeGitCache())
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--format", "xml", "--wiki", str(wiki)])

    assert result.exit_code == 2
    assert "--format" in result.stderr


def test_status_short_with_json_format_exits_2(tmp_path: Path) -> None:
    wiki = _scaffold(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--short", "--format", "json", "--wiki", str(wiki)])

    assert result.exit_code == 2
    assert "--short" in result.stderr or "incompatible" in result.stderr


def test_status_wiki_not_found_exits_1(tmp_path: Path) -> None:
    nowhere = tmp_path / "no-wiki-here"
    nowhere.mkdir()
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--wiki", str(nowhere)])

    assert result.exit_code == 1
    assert "config.yaml" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_status_long_failure_truncated_in_table_full_in_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("project-a", "main")])
    long_failure = "Connection failed: timeout\nRetried 3 times\n" + "x" * 100
    _write_state(wiki, {"project-a": RepoState(last_failure=long_failure)})
    fake = _FakeGitCache(head_local_results={"project-a": "a" * 40})
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    table_result = runner.invoke(app, ["status", "--wiki", str(wiki)])
    assert table_result.exit_code == 0, table_result.output
    assert "…" in table_result.stdout
    assert long_failure not in table_result.stdout

    json_result = runner.invoke(app, ["status", "--format", "json", "--wiki", str(wiki)])
    assert json_result.exit_code == 0, json_result.output
    parsed = json.loads(json_result.stdout)
    assert parsed["repos"][0]["last_failure"] == long_failure


def test_status_short_output_exact_format_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("repo-a", "main"), ("repo-b", "main")])
    fake = _FakeGitCache(
        head_local_results={"repo-a": "a" * 40, "repo-b": "b" * 40},
        list_commits_counts={"repo-a": 2, "repo-b": 0},
    )
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--short", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    # (b) exact format preserved via logger.print (not logger.info)
    assert len(lines) == 1
    assert lines[0].startswith("cadence-memory:")
    assert "2 repos" in lines[0]


def test_status_short_quiet_still_shows_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _scaffold(tmp_path)
    _write_config(wiki, [("repo-a", "main")])
    fake = _FakeGitCache(head_local_results={"repo-a": "a" * 40})
    _install_fake_cache(monkeypatch, fake)
    runner = CliRunner()

    # --short uses logger.print which bypasses level filter
    result = runner.invoke(app, ["--quiet", "status", "--short", "--wiki", str(wiki)])

    assert result.exit_code == 0, result.output
    assert "cadence-memory:" in result.stdout
