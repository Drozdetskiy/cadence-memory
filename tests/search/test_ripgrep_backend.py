from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from cadence_memory.search import backend as backend_mod
from cadence_memory.search.backend import RipgrepBackend, SearchError


@dataclass
class FakeCompleted:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    result: FakeCompleted,
    captured_argv: list[list[str]],
) -> None:
    def fake_run(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> FakeCompleted:
        captured_argv.append(argv)
        return result

    monkeypatch.setattr(backend_mod.subprocess, "run", fake_run)


def test_ripgrep_backend_invokes_rg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=1), captured)

    RipgrepBackend().search(query="billing", wiki_dir=tmp_path, limit=20)

    assert len(captured) == 1
    argv = captured[0]
    assert argv[0] == "rg"
    assert "--type" in argv
    md_idx = argv.index("--type")
    assert argv[md_idx + 1] == "md"
    assert "--max-count" in argv
    mc_idx = argv.index("--max-count")
    assert argv[mc_idx + 1] == "1"
    assert "--max-columns" in argv
    cols_idx = argv.index("--max-columns")
    assert argv[cols_idx + 1] == "200"
    assert "--" in argv
    dd_idx = argv.index("--")
    assert argv[dd_idx + 1] == "billing"
    assert argv[dd_idx + 2] == str(tmp_path)


def test_ripgrep_backend_passes_query_after_double_dash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=1), captured)

    RipgrepBackend().search(query="-v", wiki_dir=tmp_path, limit=20)

    argv = captured[0]
    dd_idx = argv.index("--")
    assert argv[dd_idx + 1] == "-v"


def test_ripgrep_backend_wraps_os_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("rg vanished")

    monkeypatch.setattr(backend_mod.subprocess, "run", fake_run)

    with pytest.raises(SearchError) as excinfo:
        RipgrepBackend().search(query="x", wiki_dir=tmp_path, limit=20)
    assert "ripgrep" in str(excinfo.value)
    assert "vanished" in str(excinfo.value)


def test_ripgrep_backend_skips_empty_and_malformed_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stdout = "\n".join(
        [
            f"{tmp_path}/good.md:1:hit",
            "",
            "not-a-real-line",
            f"{tmp_path}/also.md:2:another hit",
        ]
    )
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout=stdout), [])

    hits = RipgrepBackend().search(query="x", wiki_dir=tmp_path, limit=20)

    assert len(hits) == 2
    assert hits[0].snippet == "hit"
    assert hits[1].snippet == "another hit"


def test_ripgrep_backend_parses_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stdout = "\n".join(
        [
            f"{tmp_path}/projects/foo.md:12:billing reconciliation flow",
            f"{tmp_path}/data-model/user.md:3:user has billing_id: optional",
            f"{tmp_path}/architecture.md:7:billing service overview",
        ]
    )
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout=stdout), captured)

    hits = RipgrepBackend().search(query="billing", wiki_dir=tmp_path, limit=20)

    assert len(hits) == 3
    assert hits[0].path == Path(f"{tmp_path}/projects/foo.md")
    assert hits[0].score is None
    assert hits[0].snippet == "billing reconciliation flow"
    assert hits[0].backend == "ripgrep"
    assert hits[1].snippet == "user has billing_id: optional"
    assert hits[2].path == Path(f"{tmp_path}/architecture.md")


def test_ripgrep_backend_no_matches_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=1, stdout="", stderr=""), captured)

    hits = RipgrepBackend().search(query="nothing", wiki_dir=tmp_path, limit=20)

    assert hits == ()


def test_ripgrep_backend_raises_on_error_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=2, stderr="boom"), captured)

    with pytest.raises(SearchError) as excinfo:
        RipgrepBackend().search(query="x", wiki_dir=tmp_path, limit=20)
    assert "boom" in str(excinfo.value)


def test_ripgrep_backend_truncates_to_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stdout = "\n".join(f"{tmp_path}/page{i}.md:1:line {i}" for i in range(5))
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout=stdout), captured)

    hits = RipgrepBackend().search(query="line", wiki_dir=tmp_path, limit=2)

    assert len(hits) == 2


def test_ripgrep_backend_available_uses_shutil_which(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/rg" if name == "rg" else None

    monkeypatch.setattr(backend_mod.shutil, "which", fake_which)
    assert RipgrepBackend.available() is True

    monkeypatch.setattr(backend_mod.shutil, "which", lambda _name: None)
    assert RipgrepBackend.available() is False


