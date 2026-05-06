"""Unit tests for SqliteStore discover_cache methods."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from cadence_memory.store.sqlite_store import SqliteStore


def test_put_then_get_round_trip(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        payload = {
            "project": "alpha",
            "path": "doc.md",
            "kind": "pattern",
            "title": "Doc",
            "tags": ["a", "b"],
            "related": [],
        }
        store.discover_cache_put(
            path="alpha:doc.md",
            content_hash="c" * 64,
            annotation_json=json.dumps(payload, sort_keys=True),
            model="claude-cli",
        )
        got = store.discover_cache_get(
            path="alpha:doc.md",
            content_hash="c" * 64,
        )
        assert got == payload
    finally:
        store.close()


def test_get_returns_none_for_missing(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        assert store.discover_cache_get(path="alpha:doc.md", content_hash="c" * 64) is None
    finally:
        store.close()


def test_get_returns_none_on_hash_mismatch(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        payload = {"project": "alpha", "path": "doc.md", "kind": "pattern"}
        store.discover_cache_put(
            path="alpha:doc.md",
            content_hash="a" * 64,
            annotation_json=json.dumps(payload, sort_keys=True),
            model="claude-cli",
        )
        got = store.discover_cache_get(
            path="alpha:doc.md",
            content_hash="b" * 64,
        )
        assert got is None
    finally:
        store.close()


def test_put_twice_same_key_keeps_latest_payload(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        first = {"project": "alpha", "path": "doc.md", "title": "Old"}
        second = {"project": "alpha", "path": "doc.md", "title": "New"}
        store.discover_cache_put(
            path="alpha:doc.md",
            content_hash="c" * 64,
            annotation_json=json.dumps(first, sort_keys=True),
            model="claude-cli",
        )
        store.discover_cache_put(
            path="alpha:doc.md",
            content_hash="c" * 64,
            annotation_json=json.dumps(second, sort_keys=True),
            model="claude-cli",
        )
        got = store.discover_cache_get(
            path="alpha:doc.md",
            content_hash="c" * 64,
        )
        assert got == second
    finally:
        store.close()


def test_put_distinct_hashes_for_same_path_coexist(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        old_payload = {"path": "doc.md", "title": "Old"}
        new_payload = {"path": "doc.md", "title": "New"}
        store.discover_cache_put(
            path="alpha:doc.md",
            content_hash="a" * 64,
            annotation_json=json.dumps(old_payload, sort_keys=True),
            model="claude-cli",
        )
        store.discover_cache_put(
            path="alpha:doc.md",
            content_hash="b" * 64,
            annotation_json=json.dumps(new_payload, sort_keys=True),
            model="claude-cli",
        )
        got_old = store.discover_cache_get(path="alpha:doc.md", content_hash="a" * 64)
        got_new = store.discover_cache_get(path="alpha:doc.md", content_hash="b" * 64)
        assert got_old == old_payload
        assert got_new == new_payload
    finally:
        store.close()


def test_clear_empties_table(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    store = SqliteStore(db_path)
    try:
        for i in range(3):
            store.discover_cache_put(
                path=f"alpha:doc{i}.md",
                content_hash=str(i) * 64,
                annotation_json=json.dumps({"i": i}, sort_keys=True),
                model="claude-cli",
            )
        store.discover_cache_clear()
    finally:
        store.close()

    inspect = sqlite3.connect(str(db_path))
    try:
        count = inspect.execute("SELECT COUNT(*) FROM discover_cache").fetchone()[0]
        assert count == 0
    finally:
        inspect.close()


def test_put_records_generated_at_and_model(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    store = SqliteStore(db_path)
    try:
        store.discover_cache_put(
            path="alpha:doc.md",
            content_hash="c" * 64,
            annotation_json=json.dumps({"k": "v"}, sort_keys=True),
            model="claude-cli",
        )
    finally:
        store.close()

    inspect = sqlite3.connect(str(db_path))
    try:
        row = inspect.execute(
            "SELECT model, generated_at FROM discover_cache WHERE path = ?",
            ("alpha:doc.md",),
        ).fetchone()
        assert row is not None
        model, generated_at = row
        assert model == "claude-cli"
        assert isinstance(generated_at, str)
        assert generated_at != ""
    finally:
        inspect.close()
