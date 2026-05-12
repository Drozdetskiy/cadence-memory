from __future__ import annotations

import pytest

from cadence_memory.search import backend as backend_mod
from cadence_memory.search.backend import (
    NoBackendAvailableError,
    QmdBackend,
    RipgrepBackend,
    pick_backend,
)


def test_pick_backend_prefers_qmd(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    monkeypatch.setattr(backend_mod.shutil, "which", fake_which)
    chosen = pick_backend()
    assert isinstance(chosen, QmdBackend)


def test_pick_backend_falls_back_to_ripgrep(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/rg" if name == "rg" else None

    monkeypatch.setattr(backend_mod.shutil, "which", fake_which)
    chosen = pick_backend()
    assert isinstance(chosen, RipgrepBackend)


def test_pick_backend_raises_when_neither_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_mod.shutil, "which", lambda _name: None)
    with pytest.raises(NoBackendAvailableError) as excinfo:
        pick_backend()
    msg = str(excinfo.value)
    assert "qmd" in msg
    assert "ripgrep" in msg
