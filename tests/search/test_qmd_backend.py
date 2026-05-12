from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from cadence_memory.search import backend as backend_mod
from cadence_memory.search.backend import QmdBackend, SearchError


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


def test_qmd_backend_invokes_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout="[]"), captured)

    QmdBackend().search(query="billing", wiki_dir=tmp_path, limit=20)

    assert captured == [
        ["qmd", "search", "--collection", "master", "--json", "--limit", "20", "--", "billing"]
    ]


def test_qmd_backend_passes_query_after_double_dash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout="[]"), captured)

    QmdBackend().search(query="--version", wiki_dir=tmp_path, limit=20)

    argv = captured[0]
    assert argv[-2] == "--"
    assert argv[-1] == "--version"


def test_qmd_backend_wraps_os_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("qmd vanished")

    monkeypatch.setattr(backend_mod.subprocess, "run", fake_run)

    with pytest.raises(SearchError) as excinfo:
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)
    assert "qmd" in str(excinfo.value)
    assert "vanished" in str(excinfo.value)


def test_qmd_backend_available_uses_shutil_which(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/qmd" if name == "qmd" else None

    monkeypatch.setattr(backend_mod.shutil, "which", fake_which)
    assert QmdBackend.available() is True

    monkeypatch.setattr(backend_mod.shutil, "which", lambda _name: None)
    assert QmdBackend.available() is False


def test_qmd_backend_raises_on_non_dict_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout="[1, 2]"), [])

    with pytest.raises(SearchError):
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)


def test_qmd_backend_raises_on_non_string_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout='[{"path": 42}]'), [])

    with pytest.raises(SearchError):
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)


def test_qmd_backend_raises_on_non_numeric_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_run(
        monkeypatch,
        FakeCompleted(returncode=0, stdout='[{"path": "p", "score": "high"}]'),
        [],
    )

    with pytest.raises(SearchError):
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)


def test_qmd_backend_rejects_bool_score(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_run(
        monkeypatch,
        FakeCompleted(returncode=0, stdout='[{"path": "p", "score": true}]'),
        [],
    )

    with pytest.raises(SearchError):
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)


def test_qmd_backend_raises_on_non_string_snippet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_run(
        monkeypatch,
        FakeCompleted(returncode=0, stdout='[{"path": "p", "snippet": 42}]'),
        [],
    )

    with pytest.raises(SearchError):
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)


def test_qmd_backend_parses_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = json.dumps(
        [
            {
                "path": str(tmp_path / "projects" / "foo.md"),
                "score": 0.87,
                "snippet": "billing reconciliation flow",
            },
            {
                "path": str(tmp_path / "architecture.md"),
                "score": 0.42,
                "snippet": "billing service overview",
            },
        ]
    )
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout=payload), captured)

    hits = QmdBackend().search(query="billing", wiki_dir=tmp_path, limit=20)

    assert len(hits) == 2
    assert hits[0].path == tmp_path / "projects" / "foo.md"
    assert hits[0].score == 0.87
    assert hits[0].snippet == "billing reconciliation flow"
    assert hits[0].backend == "qmd"
    assert hits[1].score == 0.42
    assert hits[1].backend == "qmd"


def test_qmd_backend_handles_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout="[]"), captured)

    hits = QmdBackend().search(query="nothing", wiki_dir=tmp_path, limit=20)

    assert hits == ()


def test_qmd_backend_handles_missing_snippet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = json.dumps(
        [
            {"path": str(tmp_path / "page.md"), "score": 0.5},
        ]
    )
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout=payload), captured)

    hits = QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)

    assert len(hits) == 1
    assert hits[0].snippet == ""


def test_qmd_backend_handles_null_snippet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = json.dumps(
        [
            {"path": str(tmp_path / "page.md"), "score": 0.5, "snippet": None},
        ]
    )
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout=payload), captured)

    hits = QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)

    assert len(hits) == 1
    assert hits[0].snippet == ""


def test_qmd_backend_handles_null_score(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = json.dumps(
        [
            {"path": str(tmp_path / "page.md"), "score": None, "snippet": "hi"},
        ]
    )
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout=payload), captured)

    hits = QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)

    assert len(hits) == 1
    assert hits[0].score is None


def test_qmd_backend_resolves_relative_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = json.dumps(
        [
            {
                "path": "projects/foo/index.md",
                "score": 0.9,
                "snippet": "x",
            }
        ]
    )
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout=payload), captured)

    hits = QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)

    assert len(hits) == 1
    assert hits[0].path == (tmp_path / "projects/foo/index.md").resolve()


def test_qmd_backend_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=2, stderr="nope"), captured)

    with pytest.raises(SearchError) as excinfo:
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)
    assert "nope" in str(excinfo.value)


def test_qmd_backend_raises_on_bad_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, FakeCompleted(returncode=0, stdout="not json"), captured)

    with pytest.raises(SearchError):
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)


def test_qmd_backend_raises_on_wrong_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []
    _patch_run(
        monkeypatch,
        FakeCompleted(returncode=0, stdout='{"not": "a list"}'),
        captured,
    )

    with pytest.raises(SearchError):
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)


def test_qmd_backend_raises_on_missing_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []
    _patch_run(
        monkeypatch,
        FakeCompleted(returncode=0, stdout='[{"score": 0.5}]'),
        captured,
    )

    with pytest.raises(SearchError):
        QmdBackend().search(query="x", wiki_dir=tmp_path, limit=20)
