"""Tests for the SessionStart hook in the scaffolded settings.json (design2 §13)."""

from __future__ import annotations

import json
from pathlib import Path

from cadence_memory.wiki import scaffold_wiki

_HOOK_COMMAND = (
    "cadence-memory status --short && head -60 index.md 2>/dev/null && tail -15 log.md 2>/dev/null"
)


def test_init_writes_session_start_hook(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)

    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.is_file()
    text = settings_path.read_text(encoding="utf-8")
    assert "SessionStart" in text
    assert "cadence-memory status --short" in text


def test_settings_json_is_valid(tmp_path: Path) -> None:
    scaffold_wiki(tmp_path)

    settings_path = tmp_path / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))

    session_start = data["hooks"]["SessionStart"]
    assert isinstance(session_start, list) and len(session_start) == 1

    entry = session_start[0]
    assert entry["matcher"] == "*"
    assert entry["hooks"][0]["command"] == _HOOK_COMMAND
