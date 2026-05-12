"""Tests for `.cadence-memory/state.json` worker state (design2 §6.2)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cadence_memory.worker.state import (
    CURRENT_SCHEMA_VERSION,
    RepoState,
    StateError,
    WorkerState,
    _dt_to_str,
    _str_to_dt,
    load_state,
    save_state,
    update_repo,
)


def test_repo_state_defaults() -> None:
    repo = RepoState()
    assert repo.last_sha is None
    assert repo.last_run_at is None
    assert repo.commits_processed == 0
    assert repo.last_failure is None


def test_worker_state_defaults() -> None:
    state = WorkerState()
    assert state.version == CURRENT_SCHEMA_VERSION
    assert state.repos == {}


def test_dt_to_str_round_trip_utc() -> None:
    dt = datetime(2026, 5, 8, 14, 0, 0, tzinfo=UTC)
    s = _dt_to_str(dt)
    assert s == "2026-05-08T14:00:00+00:00"
    assert _str_to_dt(s) == dt


def test_dt_to_str_converts_offset_to_utc() -> None:
    east = timezone(timedelta(hours=3))
    dt = datetime(2026, 5, 8, 17, 0, 0, tzinfo=east)
    s = _dt_to_str(dt)
    assert s == "2026-05-08T14:00:00+00:00"
    assert _str_to_dt(s) == dt.astimezone(UTC)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        _dt_to_str(datetime(2026, 5, 8, 14, 0, 0))


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    state = load_state(tmp_path / "state.json")
    assert state == WorkerState()


def test_malformed_json_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        load_state(path)


def test_missing_version_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"repos": {}}), encoding="utf-8")
    with pytest.raises(StateError, match="version"):
        load_state(path)


def test_future_version_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": CURRENT_SCHEMA_VERSION + 1, "repos": {}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="upgrade cadence-memory"):
        load_state(path)


def test_load_v1_with_repo(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "repos": {
                    "project-a": {
                        "last_sha": "abc1234",
                        "last_run_at": "2026-05-08T14:00:00+00:00",
                        "commits_processed": 142,
                        "last_failure": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    state = load_state(path)
    assert state.version == 1
    assert state.repos == {
        "project-a": RepoState(
            last_sha="abc1234",
            last_run_at=datetime(2026, 5, 8, 14, 0, 0, tzinfo=UTC),
            commits_processed=142,
            last_failure=None,
        )
    }


def test_load_v1_null_last_failure_and_dates(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "repos": {
                    "project-b": {
                        "last_sha": None,
                        "last_run_at": None,
                        "commits_processed": 0,
                        "last_failure": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    state = load_state(path)
    assert state.repos["project-b"] == RepoState()


def test_load_repos_not_dict_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": []}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="repos"):
        load_state(path)


def test_load_top_level_not_object_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(StateError, match="JSON object"):
        load_state(path)


def test_load_repo_entry_not_dict_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": {"r": "not-an-object"}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="repo entry"):
        load_state(path)


def test_load_naive_last_run_at_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": {"r": {"last_run_at": "2026-05-08T14:00:00"}}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="timezone"):
        load_state(path)


def test_load_invalid_last_run_at_string_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": {"r": {"last_run_at": "not-a-date"}}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="ISO-8601"):
        load_state(path)


def test_load_last_run_at_wrong_type_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": {"r": {"last_run_at": 123}}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="last_run_at"):
        load_state(path)


def test_load_last_sha_wrong_type_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": {"r": {"last_sha": 5}}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="last_sha"):
        load_state(path)


def test_load_commits_processed_wrong_type_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": {"r": {"commits_processed": "many"}}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="commits_processed"):
        load_state(path)


def test_load_last_failure_wrong_type_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": {"r": {"last_failure": 7}}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="last_failure"):
        load_state(path)


def test_load_bool_version_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": True, "repos": {}}), encoding="utf-8")
    with pytest.raises(StateError, match="non-integer 'version'"):
        load_state(path)


def test_load_bool_commits_processed_errors(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "repos": {"r": {"commits_processed": True}}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="commits_processed"):
        load_state(path)


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = WorkerState(
        repos={
            "project-a": RepoState(
                last_sha="abc1234",
                last_run_at=datetime(2026, 5, 8, 14, 0, 0, tzinfo=UTC),
                commits_processed=42,
                last_failure=None,
            ),
        }
    )
    save_state(path, state)
    assert load_state(path) == state


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "state.json"
    save_state(path, WorkerState())
    assert path.is_file()
    assert load_state(path) == WorkerState()


def test_file_diff_stable(tmp_path: Path) -> None:
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    state = WorkerState(
        repos={
            "z-repo": RepoState(last_sha="z", commits_processed=1),
            "a-repo": RepoState(last_sha="a", commits_processed=2),
        }
    )
    save_state(path_a, state)
    save_state(path_b, state)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_atomic_write_no_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    original = WorkerState(repos={"keep": RepoState(last_sha="original")})
    save_state(path, original)
    original_bytes = path.read_bytes()

    def boom(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        save_state(path, WorkerState(repos={"keep": RepoState(last_sha="changed")}))

    assert path.read_bytes() == original_bytes
    tmp = path.with_suffix(path.suffix + ".tmp")
    assert tmp.exists()


def test_datetime_serialization_preserves_utc(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    east = timezone(timedelta(hours=3))
    dt = datetime(2026, 5, 8, 17, 0, 0, tzinfo=east)
    state = WorkerState(repos={"r": RepoState(last_run_at=dt)})
    save_state(path, state)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["repos"]["r"]["last_run_at"] == "2026-05-08T14:00:00+00:00"
    loaded = load_state(path)
    assert loaded.repos["r"].last_run_at == dt.astimezone(UTC)


def test_second_precision_truncation(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    dt = datetime(2026, 5, 8, 14, 0, 0, 123456, tzinfo=UTC)
    state = WorkerState(repos={"r": RepoState(last_run_at=dt)})
    save_state(path, state)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["repos"]["r"]["last_run_at"] == "2026-05-08T14:00:00+00:00"
    loaded = load_state(path)
    assert loaded.repos["r"].last_run_at == dt.replace(microsecond=0)


def test_update_repo_creates_new_entry() -> None:
    state = WorkerState()
    updated = update_repo(
        state,
        name="project-a",
        last_sha="abc",
        commits_processed_delta=3,
    )
    assert updated.repos == {
        "project-a": RepoState(last_sha="abc", commits_processed=3),
    }


def test_update_repo_sentinel_preserves_field() -> None:
    state = WorkerState(
        repos={"r": RepoState(last_sha="old", last_failure="boom")},
    )
    updated = update_repo(state, name="r", last_sha="new")
    assert updated.repos["r"].last_sha == "new"
    assert updated.repos["r"].last_failure == "boom"


def test_update_repo_clear_failure() -> None:
    state = WorkerState(repos={"r": RepoState(last_failure="boom")})
    updated = update_repo(state, name="r", last_failure=None)
    assert updated.repos["r"].last_failure is None


def test_update_repo_clear_last_sha() -> None:
    state = WorkerState(repos={"r": RepoState(last_sha="abc")})
    updated = update_repo(state, name="r", last_sha=None)
    assert updated.repos["r"].last_sha is None


def test_commits_processed_delta_accumulates() -> None:
    state = WorkerState()
    state = update_repo(state, name="r", commits_processed_delta=2)
    state = update_repo(state, name="r", commits_processed_delta=5)
    state = update_repo(state, name="r", commits_processed_delta=1)
    assert state.repos["r"].commits_processed == 8


def test_update_repo_returns_new_instance() -> None:
    original = WorkerState(repos={"r": RepoState(last_sha="orig")})
    original_repos_snapshot = dict(original.repos)
    updated = update_repo(original, name="r", last_sha="new")
    assert updated is not original
    assert updated.repos is not original.repos
    assert original.repos == original_repos_snapshot
    assert original.repos["r"].last_sha == "orig"
