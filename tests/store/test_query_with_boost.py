"""Tests for SqliteStore.query identifier-based ranking boost."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from cadence_memory.documents.chunker import Chunk
from cadence_memory.store.interface import Mention, StoredDocument
from cadence_memory.store.sqlite_store import SqliteStore


def _make_doc(
    *,
    id: str,
    body: str,
    title: str = "Doc",
    project: str | None = "proj",
    kind: str = "pattern",
    source_type: Literal["project", "global", "ephemeral"] = "project",
) -> StoredDocument:
    return StoredDocument(
        id=id,
        source_type=source_type,
        project=project,
        abs_path=f"/abs/{id}",
        rel_path=id.split(":", 1)[1] if ":" in id else id,
        kind=kind,
        title=title,
        body=body,
        tags=(),
        related=(),
        content_hash="c" * 64,
        frontmatter_hash="f" * 64,
        annotation_hash="a" * 64,
        mtime=1700000000,
        indexed_at="2026-01-01T00:00:00Z",
    )


def _seed_chunk(
    store: SqliteStore,
    *,
    doc_id: str,
    body: str,
    title: str = "Doc",
    slug: str = "_preamble",
) -> str:
    store.upsert(_make_doc(id=doc_id, body=body, title=title))
    store.upsert_chunks(doc_id, [Chunk(slug=slug, heading_path=(), body=body, order=0)])
    return f"{doc_id}#{slug}"


def test_schema_mention_boosts_chunk_above_peers(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        baseline_body = "BillingEntity field BillingEntity field BillingEntity field"
        boosted_body = "BillingEntity field appears once here"
        for i in range(4):
            _seed_chunk(
                store,
                doc_id=f"proj:bg{i}.md",
                body=baseline_body,
                title=f"Background {i}",
            )
        boosted_id = _seed_chunk(
            store,
            doc_id="proj:target.md",
            body=boosted_body,
            title="Target",
        )
        store.upsert_mentions(
            boosted_id,
            [Mention(target="BillingEntity", target_kind="schema")],
        )

        with_boost = store.query("BillingEntity field", limit=5)
        assert with_boost, "expected non-empty FTS5 result"
        assert with_boost[0].id == boosted_id
        assert with_boost[0].score_boost == 5.0

        without_boost = store.query("BillingEntity field", limit=5, boost=False)
        assert all(c.score_boost == 0.0 for c in without_boost)
        assert without_boost[0].id != boosted_id
    finally:
        store.close()


def test_identifier_free_query_unchanged_by_boost_flag(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        for i in range(3):
            _seed_chunk(
                store,
                doc_id=f"proj:doc{i}.md",
                body=f"some plain words appear here number {i}",
                title=f"Doc {i}",
            )

        with_boost = store.query("some plain words", limit=5)
        without_boost = store.query("some plain words", limit=5, boost=False)
        assert [c.id for c in with_boost] == [c.id for c in without_boost]
        for chunk in with_boost + without_boost:
            assert chunk.score_boost == 0.0
    finally:
        store.close()


def test_jira_id_substring_boosts_chunk(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        baseline_body = "release notes for the latest milestone discussion"
        boosted_body = "release notes ZEAL-10367 covers the bugfix"
        for i in range(3):
            _seed_chunk(
                store,
                doc_id=f"proj:n{i}.md",
                body=baseline_body,
                title=f"Notes {i}",
            )
        boosted_id = _seed_chunk(
            store,
            doc_id="proj:jira.md",
            body=boosted_body,
            title="Jira Doc",
        )

        results = store.query('"ZEAL-10367" release', limit=5)
        assert results, "expected non-empty FTS5 result"
        assert results[0].id == boosted_id
        assert results[0].score_boost == 5.0
    finally:
        store.close()


def test_multi_kind_boost_is_sum_of_kinds(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        baseline_body = "BillingEntity field appears in this background note"
        for i in range(3):
            _seed_chunk(
                store,
                doc_id=f"proj:bg{i}.md",
                body=baseline_body,
                title=f"Background {i}",
            )
        boosted_id = _seed_chunk(
            store,
            doc_id="proj:multi.md",
            body="BillingEntity field with ZEAL-10367 attached",
            title="Multi",
        )
        store.upsert_mentions(
            boosted_id,
            [Mention(target="BillingEntity", target_kind="schema")],
        )

        results = store.query('BillingEntity "ZEAL-10367" field', limit=5)
        assert results, "expected non-empty FTS5 result"
        assert results[0].id == boosted_id
        assert results[0].score_boost == 10.0
    finally:
        store.close()


def test_jira_substring_does_not_match_longer_id(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        longer_id = _seed_chunk(
            store,
            doc_id="proj:longer.md",
            body="release notes ZEAL 10367 mentions ZEAL-103670 longer fix",
            title="Longer ID",
        )
        exact = _seed_chunk(
            store,
            doc_id="proj:exact.md",
            body="release notes ZEAL-10367 covers the fix",
            title="Exact",
        )

        results = store.query('release "ZEAL-10367"', limit=5)
        assert results, "expected non-empty FTS5 result"
        by_id = {c.id: c for c in results}
        assert exact in by_id and longer_id in by_id, (
            "both chunks should be returned by FTS5 before boost"
        )
        assert by_id[exact].score_boost == 5.0
        assert by_id[longer_id].score_boost == 0.0
    finally:
        store.close()


def test_score_populated_and_boost_zero_when_disabled(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test.db")
    try:
        boosted_id = _seed_chunk(
            store,
            doc_id="proj:doc.md",
            body="BillingEntity field appears here once",
            title="Doc",
        )
        store.upsert_mentions(
            boosted_id,
            [Mention(target="BillingEntity", target_kind="schema")],
        )

        without_boost = store.query("BillingEntity field", limit=5, boost=False)
        assert without_boost
        for chunk in without_boost:
            assert chunk.score_boost == 0.0
            assert isinstance(chunk.score, float)
            assert chunk.score < 0.0

        with_boost = store.query("BillingEntity field", limit=5)
        for chunk in with_boost:
            assert isinstance(chunk.score, float)
    finally:
        store.close()
