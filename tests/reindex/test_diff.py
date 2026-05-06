"""Unit tests for the reindex diff (dry-run)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from cadence_memory.config import (
    AnnotationsConfig,
    Config,
    Defaults,
    DiscoverConfig,
    DocumentEntry,
    GlobalsConfig,
    ProjectConfig,
)
from cadence_memory.documents.chunker import Chunk
from cadence_memory.reindex.diff import diff
from cadence_memory.reindex.engine import ReindexError, reindex
from cadence_memory.store.interface import StoredChunk, StoredDocument
from cadence_memory.store.sqlite_store import SqliteStore


def _make_config(*projects: ProjectConfig) -> Config:
    return Config(
        projects=tuple(projects),
        globals=GlobalsConfig(include=("**/*.md",), exclude=()),
        defaults=Defaults(kind="doc"),
        commit_index=False,
    )


def _make_project(name: str, path: Path) -> ProjectConfig:
    return ProjectConfig(
        name=name,
        path=path,
        exclude=(),
        discover=DiscoverConfig(kind_rules=()),
    )


def _entry(
    *,
    id: str,
    project: str | None,
    path: str,
    kind: str | None = None,
    title: str | None = None,
    tags: tuple[str, ...] = (),
    related: tuple[str, ...] = (),
    optional: bool = False,
) -> DocumentEntry:
    return DocumentEntry(
        id=id,
        project=project,
        path=path,
        kind=kind,
        title=title,
        tags=tags,
        related=related,
        optional=optional,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _CountingStore:
    """Wrap a real SqliteStore and count upsert/delete calls."""

    def __init__(self, inner: SqliteStore) -> None:
        self.inner = inner
        self.upsert_calls: list[StoredDocument] = []
        self.delete_calls: list[str] = []
        self.upsert_chunks_calls: list[tuple[str, tuple[Chunk, ...]]] = []

    def upsert(self, doc: StoredDocument) -> None:
        self.upsert_calls.append(doc)
        self.inner.upsert(doc)

    def delete(self, doc_id: str) -> None:
        self.delete_calls.append(doc_id)
        self.inner.delete(doc_id)

    def get(self, doc_id: str) -> StoredDocument | None:
        return self.inner.get(doc_id)

    def list(
        self,
        *,
        kind: str | None = None,
        project: str | None = None,
        source_type: str | None = None,
    ) -> list[StoredDocument]:
        return self.inner.list(kind=kind, project=project, source_type=source_type)

    def upsert_chunks(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        self.upsert_chunks_calls.append((document_id, tuple(chunks)))
        self.inner.upsert_chunks(document_id, chunks)

    def get_chunks(self, document_id: str) -> list[StoredChunk]:
        return self.inner.get_chunks(document_id)

    def get_chunk(self, chunk_id: str) -> StoredChunk | None:
        return self.inner.get_chunk(chunk_id)

    def query(
        self,
        text: str,
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> list[StoredChunk]:
        return self.inner.query(text, kind=kind, project=project, limit=limit)

    def all_ids(self) -> set[str]:
        return self.inner.all_ids()

    def upsert_chunk_enrichment(self, chunk_id: str, enrichment_text: str) -> None:
        self.inner.upsert_chunk_enrichment(chunk_id, enrichment_text)

    def enrichment_cache_get(self, content_hash: str) -> dict[str, object] | None:
        return self.inner.enrichment_cache_get(content_hash)

    def enrichment_cache_put(
        self,
        *,
        content_hash: str,
        enrichment_json: str,
        model: str,
        generated_at: str,
    ) -> None:
        self.inner.enrichment_cache_put(
            content_hash=content_hash,
            enrichment_json=enrichment_json,
            model=model,
            generated_at=generated_at,
        )

    def close(self) -> None:
        self.inner.close()


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, _CountingStore]:
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()
    db_path = store_dir / "index.sqlite"
    inner = SqliteStore(db_path)
    counting = _CountingStore(inner)
    return store_dir, project_dir, db_path, counting


def test_diff_classifies_new_entry_as_inserted_without_writing(tmp_path: Path) -> None:
    store_dir, project_dir, db_path, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        before_mtime = db_path.stat().st_mtime_ns
        before_ids = store.all_ids()

        result = diff(config=cfg, annotations=ann, store=store, store_dir=store_dir)

        assert result.inserted == ("proj:README.md",)
        assert result.updated_content == ()
        assert result.updated_metadata_only == ()
        assert result.deleted == ()
        assert result.skipped_optional_missing == ()
        assert store.upsert_calls == []
        assert store.delete_calls == []
        assert store.upsert_chunks_calls == []
        assert store.all_ids() == before_ids
        assert db_path.stat().st_mtime_ns == before_mtime
    finally:
        store.close()


def test_diff_classifies_body_change_as_updated_content(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "README.md"
        _write(md, "# T\n\nold body\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir)
        before_upserts = len(store.upsert_calls)
        before_deletes = len(store.delete_calls)

        _write(md, "# T\n\nnew body\n")
        result = diff(config=cfg, annotations=ann, store=store, store_dir=store_dir)

        assert result.updated_content == ("proj:README.md",)
        assert result.inserted == ()
        assert len(store.upsert_calls) == before_upserts
        assert len(store.delete_calls) == before_deletes
    finally:
        store.close()


def test_diff_classifies_metadata_only_change(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "doc.md"
        _write(md, "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann_v1 = AnnotationsConfig(
            documents=(_entry(id="proj:doc.md", project="proj", path="doc.md", tags=("a",)),)
        )
        ann_v2 = AnnotationsConfig(
            documents=(
                _entry(
                    id="proj:doc.md",
                    project="proj",
                    path="doc.md",
                    tags=("a", "b"),
                ),
            )
        )

        reindex(config=cfg, annotations=ann_v1, store=store, store_dir=store_dir)
        before_upserts = len(store.upsert_calls)
        before_deletes = len(store.delete_calls)

        result = diff(config=cfg, annotations=ann_v2, store=store, store_dir=store_dir)

        assert result.updated_metadata_only == ("proj:doc.md",)
        assert result.updated_content == ()
        assert len(store.upsert_calls) == before_upserts
        assert len(store.delete_calls) == before_deletes
    finally:
        store.close()


def test_diff_reports_deleted_without_calling_delete(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "a.md", "# A\n")
        _write(project_dir / "b.md", "# B\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann_full = AnnotationsConfig(
            documents=(
                _entry(id="proj:a.md", project="proj", path="a.md"),
                _entry(id="proj:b.md", project="proj", path="b.md"),
            )
        )
        ann_partial = AnnotationsConfig(
            documents=(_entry(id="proj:a.md", project="proj", path="a.md"),)
        )

        reindex(config=cfg, annotations=ann_full, store=store, store_dir=store_dir)
        before_upserts = len(store.upsert_calls)
        before_deletes = len(store.delete_calls)
        before_ids = store.all_ids()

        result = diff(config=cfg, annotations=ann_partial, store=store, store_dir=store_dir)

        assert result.deleted == ("proj:b.md",)
        assert len(store.upsert_calls) == before_upserts
        assert len(store.delete_calls) == before_deletes
        assert store.all_ids() == before_ids
        assert store.get("proj:b.md") is not None
    finally:
        store.close()


def test_diff_raises_on_missing_required_file(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:missing.md", project="proj", path="missing.md"),)
        )
        with pytest.raises(ReindexError) as info:
            diff(config=cfg, annotations=ann, store=store, store_dir=store_dir)
        assert "proj:missing.md" in str(info.value)
        assert store.upsert_calls == []
        assert store.delete_calls == []
    finally:
        store.close()


def test_diff_skips_missing_optional(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(
                _entry(
                    id="proj:optional.md",
                    project="proj",
                    path="optional.md",
                    optional=True,
                ),
            )
        )
        result = diff(config=cfg, annotations=ann, store=store, store_dir=store_dir)
        assert result.skipped_optional_missing == ("proj:optional.md",)
        assert store.upsert_calls == []
        assert store.delete_calls == []
    finally:
        store.close()


def test_diff_unchanged_entry_appears_in_no_bucket(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir)
        before_upserts = len(store.upsert_calls)

        result = diff(config=cfg, annotations=ann, store=store, store_dir=store_dir)

        assert result.inserted == ()
        assert result.updated_content == ()
        assert result.updated_metadata_only == ()
        assert result.deleted == ()
        assert len(store.upsert_calls) == before_upserts
    finally:
        store.close()


def test_diff_does_not_report_ephemeral_rows_as_deleted(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        eph_doc = StoredDocument(
            id="eph:note-1",
            source_type="ephemeral",
            project=None,
            abs_path=str(store_dir / "ephemeral" / "note-1.md"),
            rel_path="ephemeral/note-1.md",
            kind="task",
            title="ephemeral note",
            body="scratch\n",
            tags=(),
            related=(),
            content_hash="x" * 64,
            frontmatter_hash="y" * 64,
            annotation_hash="z" * 64,
            mtime=0,
            indexed_at="2026-01-01T00:00:00+00:00",
        )
        store.upsert(eph_doc)
        store.upsert_calls.clear()

        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        result = diff(config=cfg, annotations=ann, store=store, store_dir=store_dir)

        assert result.deleted == ()
        assert store.delete_calls == []
    finally:
        store.close()


def test_diff_matches_reindex_classification(tmp_path: Path) -> None:
    """diff() must produce the same classification as reindex() for the same input."""
    # build two parallel setups (separate stores) so reindex can mutate one
    # while diff observes the other in identical pre-state.
    store_dir_a = tmp_path / "store_a"
    store_dir_b = tmp_path / "store_b"
    project_dir = tmp_path / "proj"
    store_dir_a.mkdir()
    store_dir_b.mkdir()
    project_dir.mkdir()
    _write(project_dir / "a.md", "# A\n\nbody-a\n")
    _write(project_dir / "b.md", "# B\n\nbody-b\n")
    _write(project_dir / "c.md", "# C\n\nbody-c\n")

    cfg = _make_config(_make_project("proj", project_dir))
    ann_initial = AnnotationsConfig(
        documents=(
            _entry(id="proj:a.md", project="proj", path="a.md"),
            _entry(id="proj:b.md", project="proj", path="b.md", tags=("v1",)),
            _entry(id="proj:c.md", project="proj", path="c.md"),
        )
    )
    # next-generation annotations: a unchanged, b metadata change, c removed,
    # d new (file present), e missing+optional.
    _write(project_dir / "d.md", "# D\n\nbody-d\n")
    ann_next = AnnotationsConfig(
        documents=(
            _entry(id="proj:a.md", project="proj", path="a.md"),
            _entry(id="proj:b.md", project="proj", path="b.md", tags=("v1", "v2")),
            _entry(id="proj:d.md", project="proj", path="d.md"),
            _entry(
                id="proj:e.md",
                project="proj",
                path="e.md",
                optional=True,
            ),
        )
    )

    inner_a = SqliteStore(store_dir_a / "index.sqlite")
    store_a = _CountingStore(inner_a)
    inner_b = SqliteStore(store_dir_b / "index.sqlite")
    store_b = _CountingStore(inner_b)
    try:
        # establish identical baselines on both stores.
        reindex(config=cfg, annotations=ann_initial, store=store_a, store_dir=store_dir_a)
        reindex(config=cfg, annotations=ann_initial, store=store_b, store_dir=store_dir_b)

        # also change a.md body so it triggers updated_content on both stores.
        _write(project_dir / "a.md", "# A\n\nbody-a-changed\n")

        before_a_upserts = len(store_a.upsert_calls)
        before_a_deletes = len(store_a.delete_calls)
        before_a_ids = store_a.all_ids()

        diff_result = diff(config=cfg, annotations=ann_next, store=store_a, store_dir=store_dir_a)
        reindex_result = reindex(
            config=cfg, annotations=ann_next, store=store_b, store_dir=store_dir_b
        )

        assert diff_result.inserted == reindex_result.inserted
        assert diff_result.updated_content == reindex_result.updated_content
        assert diff_result.updated_metadata_only == reindex_result.updated_metadata_only
        assert diff_result.deleted == reindex_result.deleted
        assert diff_result.skipped_optional_missing == reindex_result.skipped_optional_missing
        assert len(store_a.upsert_calls) == before_a_upserts
        assert len(store_a.delete_calls) == before_a_deletes
        assert store_a.all_ids() == before_a_ids
    finally:
        store_a.close()
        store_b.close()
