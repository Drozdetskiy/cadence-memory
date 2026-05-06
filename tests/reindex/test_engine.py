"""Unit tests for the reindex engine."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
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
from cadence_memory.enrichment.interface import EnrichmentResult
from cadence_memory.reindex.engine import ReindexError, reindex
from cadence_memory.store.interface import Mention, StoredChunk, StoredDocument
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


def _frozen_now() -> datetime:
    return datetime(2026, 5, 5, 12, 30, 0, tzinfo=UTC)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _CountingStore:
    """Wrap a real SqliteStore and count upsert/delete calls per id."""

    def __init__(self, inner: SqliteStore) -> None:
        self.inner = inner
        self.upsert_calls: list[StoredDocument] = []
        self.delete_calls: list[str] = []
        self.upsert_chunks_calls: list[tuple[str, tuple[Chunk, ...]]] = []
        self.upsert_chunk_enrichment_calls: list[tuple[str, str]] = []
        self.enrichment_cache_get_calls: list[str] = []
        self.enrichment_cache_put_calls: list[dict[str, str]] = []
        self.upsert_mentions_calls: list[tuple[str, tuple[Mention, ...]]] = []

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

    def upsert_mentions(self, chunk_id: str, mentions: Sequence[Mention]) -> None:
        self.upsert_mentions_calls.append((chunk_id, tuple(mentions)))
        self.inner.upsert_mentions(chunk_id, mentions)

    def get_mentions(self, chunk_id: str) -> list[Mention]:
        return self.inner.get_mentions(chunk_id)

    def find_backlinks(self, target: str, *, target_kind: str | None = None) -> list[StoredChunk]:
        return self.inner.find_backlinks(target, target_kind=target_kind)

    def upsert_chunk_enrichment(self, chunk_id: str, enrichment_text: str) -> None:
        self.upsert_chunk_enrichment_calls.append((chunk_id, enrichment_text))
        self.inner.upsert_chunk_enrichment(chunk_id, enrichment_text)

    def enrichment_cache_get(self, content_hash: str) -> dict[str, object] | None:
        self.enrichment_cache_get_calls.append(content_hash)
        return self.inner.enrichment_cache_get(content_hash)

    def enrichment_cache_put(
        self,
        *,
        content_hash: str,
        enrichment_json: str,
        model: str,
        generated_at: str,
    ) -> None:
        self.enrichment_cache_put_calls.append(
            {
                "content_hash": content_hash,
                "enrichment_json": enrichment_json,
                "model": model,
                "generated_at": generated_at,
            }
        )
        self.inner.enrichment_cache_put(
            content_hash=content_hash,
            enrichment_json=enrichment_json,
            model=model,
            generated_at=generated_at,
        )

    def close(self) -> None:
        self.inner.close()


class _StubEnricher:
    """Deterministic enricher that records every call."""

    def __init__(
        self,
        *,
        keywords: tuple[str, ...] = ("вебхук", "webhook", "сигнал"),
        questions: tuple[str, ...] = ("How are webhooks processed?",),
        alt_phrasings: tuple[str, ...] = ("event delivery",),
        model: str = "claude-haiku-4-5",
    ) -> None:
        self._keywords = keywords
        self._questions = questions
        self._alt_phrasings = alt_phrasings
        self.model = model
        self.calls: list[dict[str, object]] = []

    def enrich_chunk(
        self,
        *,
        title: str,
        heading_path: tuple[str, ...],
        body: str,
        summary: str | None,
    ) -> EnrichmentResult:
        self.calls.append(
            {
                "title": title,
                "heading_path": heading_path,
                "body": body,
                "summary": summary,
            }
        )
        return EnrichmentResult(
            keywords=self._keywords,
            questions=self._questions,
            alt_phrasings=self._alt_phrasings,
            model=self.model,
            generated_at="2026-05-05T12:30:00+00:00",
        )


def _setup(
    tmp_path: Path,
) -> tuple[Path, Path, Path, _CountingStore]:
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()
    db_path = store_dir / "index.sqlite"
    inner = SqliteStore(db_path)
    counting = _CountingStore(inner)
    return store_dir, project_dir, db_path, counting


def test_new_entry_inserted(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# Title\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )
        assert result.inserted == ("proj:README.md",)
        assert result.updated_content == ()
        assert result.updated_metadata_only == ()
        assert result.deleted == ()
        assert result.skipped_optional_missing == ()
        got = store.get("proj:README.md")
        assert got is not None
        assert got.body == "# Title\n\nbody\n"
        assert got.title == "Title"
        assert got.kind == "doc"
        assert got.source_type == "project"
        assert got.project == "proj"
        assert len(store.upsert_chunks_calls) == 1
        chunked_id, chunks = store.upsert_chunks_calls[0]
        assert chunked_id == "proj:README.md"
        assert len(chunks) >= 1
        stored_chunks = store.get_chunks("proj:README.md")
        assert len(stored_chunks) == len(chunks)
    finally:
        store.close()


def test_body_change_updates_content(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "README.md"
        _write(md, "# Title\n\nold body\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)
        upsert_count_after_first = len(store.upsert_calls)
        upsert_chunks_count_after_first = len(store.upsert_chunks_calls)

        _write(md, "# Title\n\nnew body content\n")
        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.updated_content == ("proj:README.md",)
        assert result.inserted == ()
        assert len(store.upsert_calls) == upsert_count_after_first + 1
        assert len(store.upsert_chunks_calls) == upsert_chunks_count_after_first + 1
        got = store.get("proj:README.md")
        assert got is not None
        assert got.body == "# Title\n\nnew body content\n"

        hits = store.query("body content")
        assert any(h.document_id == "proj:README.md" for h in hits)
        old_hits = store.query("old")
        assert all(h.document_id != "proj:README.md" for h in old_hits)
    finally:
        store.close()


def test_frontmatter_change_metadata_only(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "doc.md"
        _write(md, "---\nkind: pattern\n---\n# Title\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:doc.md", project="proj", path="doc.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)
        first_doc = store.upsert_calls[-1]
        assert first_doc.body == "# Title\n\nbody\n"
        assert first_doc.kind == "pattern"
        chunks_after_first = len(store.upsert_chunks_calls)

        _write(md, "---\nkind: service\n---\n# Title\n\nbody\n")
        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.updated_metadata_only == ("proj:doc.md",)
        assert result.updated_content == ()
        second_doc = store.upsert_calls[-1]
        assert second_doc.body == first_doc.body
        assert second_doc.kind == "service"
        assert len(store.upsert_chunks_calls) == chunks_after_first
    finally:
        store.close()


def test_annotations_change_metadata_only(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "doc.md"
        _write(md, "# Title\n\nbody\n")
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

        reindex(config=cfg, annotations=ann_v1, store=store, store_dir=store_dir, now=_frozen_now)
        chunks_after_first = len(store.upsert_chunks_calls)
        result = reindex(
            config=cfg, annotations=ann_v2, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.updated_metadata_only == ("proj:doc.md",)
        assert result.updated_content == ()
        got = store.get("proj:doc.md")
        assert got is not None
        assert got.tags == ("a", "b")
        assert len(store.upsert_chunks_calls) == chunks_after_first
    finally:
        store.close()


def test_entry_removed_is_deleted(tmp_path: Path) -> None:
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

        reindex(config=cfg, annotations=ann_full, store=store, store_dir=store_dir, now=_frozen_now)
        result = reindex(
            config=cfg,
            annotations=ann_partial,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
        )

        assert result.deleted == ("proj:b.md",)
        assert store.get("proj:b.md") is None
        assert store.get("proj:a.md") is not None
        assert "proj:b.md" in store.delete_calls
    finally:
        store.close()


def test_missing_required_file_raises(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:missing.md", project="proj", path="missing.md"),)
        )
        with pytest.raises(ReindexError) as info:
            reindex(
                config=cfg,
                annotations=ann,
                store=store,
                store_dir=store_dir,
                now=_frozen_now,
            )
        assert "proj:missing.md" in str(info.value)
        assert "missing.md" in str(info.value)
        assert store.upsert_calls == []
        assert store.delete_calls == []
    finally:
        store.close()


def test_missing_optional_file_is_skipped(tmp_path: Path) -> None:
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
        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )
        assert result.skipped_optional_missing == ("proj:optional.md",)
        assert result.inserted == ()
        assert store.upsert_calls == []
    finally:
        store.close()


def test_global_document_resolves_under_store_dir(tmp_path: Path) -> None:
    store_dir, _project_dir, _db, store = _setup(tmp_path)
    try:
        _write(store_dir / "workflows" / "deploy.md", "# Deploy\n\nsteps\n")
        cfg = _make_config()
        ann = AnnotationsConfig(
            documents=(
                _entry(
                    id=":workflows/deploy.md",
                    project=None,
                    path="workflows/deploy.md",
                ),
            )
        )

        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )
        assert result.inserted == (":workflows/deploy.md",)
        got = store.get(":workflows/deploy.md")
        assert got is not None
        assert got.source_type == "global"
        assert got.project is None
        assert got.abs_path == str((store_dir / "workflows" / "deploy.md").resolve())
    finally:
        store.close()


def test_proposed_file_is_ignored(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        _write(
            store_dir / "annotations-config.yaml.proposed",
            "this is: not: even: valid yaml ::: -\n",
        )
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )
        assert result.inserted == ("proj:README.md",)
    finally:
        store.close()


def test_unchanged_entry_not_upserted(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)
        upsert_count_after_first = len(store.upsert_calls)

        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )
        assert result.inserted == ()
        assert result.updated_content == ()
        assert result.updated_metadata_only == ()
        assert result.deleted == ()
        assert len(store.upsert_calls) == upsert_count_after_first
    finally:
        store.close()


def test_duplicate_resolved_path_raises(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(
                _entry(id="proj:README.md", project="proj", path="README.md"),
                _entry(id="proj:nested/../README.md", project="proj", path="nested/../README.md"),
            )
        )
        with pytest.raises(ReindexError) as info:
            reindex(
                config=cfg,
                annotations=ann,
                store=store,
                store_dir=store_dir,
                now=_frozen_now,
            )
        assert "physical path" in str(info.value)
        assert store.upsert_calls == []
        assert store.delete_calls == []
    finally:
        store.close()


def test_unknown_project_raises_reindex_error(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="other:README.md", project="other", path="README.md"),)
        )
        with pytest.raises(ReindexError) as info:
            reindex(
                config=cfg,
                annotations=ann,
                store=store,
                store_dir=store_dir,
                now=_frozen_now,
            )
        assert "other" in str(info.value)
        assert "other:README.md" in str(info.value)
        assert store.upsert_calls == []
    finally:
        store.close()


def test_malformed_frontmatter_raises_reindex_error(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "broken.md"
        _write(md, "---\nkind: [unterminated\n---\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:broken.md", project="proj", path="broken.md"),)
        )
        with pytest.raises(ReindexError) as info:
            reindex(
                config=cfg,
                annotations=ann,
                store=store,
                store_dir=store_dir,
                now=_frozen_now,
            )
        assert "proj:broken.md" in str(info.value)
        assert store.upsert_calls == []
    finally:
        store.close()


def test_invalid_frontmatter_tags_raises_reindex_error(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "bad-tags.md"
        _write(md, "---\ntags: not-a-list\n---\n# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:bad-tags.md", project="proj", path="bad-tags.md"),)
        )
        with pytest.raises(ReindexError) as info:
            reindex(
                config=cfg,
                annotations=ann,
                store=store,
                store_dir=store_dir,
                now=_frozen_now,
            )
        assert "proj:bad-tags.md" in str(info.value)
        assert store.upsert_calls == []
    finally:
        store.close()


def test_path_change_with_unchanged_content_updates_paths(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        original = project_dir / "README.md"
        _write(original, "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann_v1 = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )
        reindex(config=cfg, annotations=ann_v1, store=store, store_dir=store_dir, now=_frozen_now)
        upsert_count_after_first = len(store.upsert_calls)

        moved = project_dir / "docs" / "README.md"
        _write(moved, "# T\n\nbody\n")
        original.unlink()
        ann_v2 = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="docs/README.md"),)
        )

        result = reindex(
            config=cfg, annotations=ann_v2, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.updated_metadata_only == ("proj:README.md",)
        assert result.updated_content == ()
        assert len(store.upsert_calls) == upsert_count_after_first + 1
        got = store.get("proj:README.md")
        assert got is not None
        assert got.rel_path == "docs/README.md"
        assert got.abs_path == str(moved.resolve())
    finally:
        store.close()


def test_reindex_does_not_delete_ephemeral_rows(tmp_path: Path) -> None:
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

        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.deleted == ()
        assert "eph:note-1" not in store.delete_calls
        assert store.get("eph:note-1") is not None
    finally:
        store.close()


def test_parse_file_oserror_wrapped_as_reindex_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        def _boom(_path: Path) -> None:
            raise PermissionError("read denied")

        monkeypatch.setattr("cadence_memory.reindex.engine.parse_file", _boom)

        with pytest.raises(ReindexError) as info:
            reindex(
                config=cfg,
                annotations=ann,
                store=store,
                store_dir=store_dir,
                now=_frozen_now,
            )
        assert "proj:README.md" in str(info.value)
        assert "read denied" in str(info.value)
        assert store.upsert_calls == []
    finally:
        store.close()


def test_stat_oserror_wrapped_as_reindex_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )

        import os as _os

        real_stat = _os.stat
        calls = {"n": 0}

        def _stat_boom(path: object, *args: object, **kwargs: object) -> object:
            if isinstance(path, (str, Path)) and str(path).endswith("README.md"):
                calls["n"] += 1
                if calls["n"] >= 2:
                    raise PermissionError("stat denied")
            return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("cadence_memory.reindex.engine.os.stat", _stat_boom)

        with pytest.raises(ReindexError) as info:
            reindex(
                config=cfg,
                annotations=ann,
                store=store,
                store_dir=store_dir,
                now=_frozen_now,
            )
        assert "proj:README.md" in str(info.value)
        assert "stat denied" in str(info.value)
        assert store.upsert_calls == []
    finally:
        store.close()


def test_injected_now_controls_indexed_at(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "README.md", "# T\n\nbody\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:README.md", project="proj", path="README.md"),)
        )
        moment = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
        reindex(
            config=cfg,
            annotations=ann,
            store=store,
            store_dir=store_dir,
            now=lambda: moment,
        )
        got = store.get("proj:README.md")
        assert got is not None
        assert got.indexed_at == moment.isoformat()
    finally:
        store.close()


def test_query_returns_chunk_after_reindex(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(
            project_dir / "guide.md",
            "# Guide\n\n## Introduction\n\nSome introductory text.\n\n"
            "## Deployment\n\nThe deployment uses kubernetes manifests.\n",
        )
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:guide.md", project="proj", path="guide.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)

        hits = store.query("kubernetes manifests")
        assert len(hits) >= 1
        assert any(h.document_id == "proj:guide.md" for h in hits)
        deployment_hit = next(h for h in hits if h.document_id == "proj:guide.md")
        assert "kubernetes" in deployment_hit.body.lower()
        assert deployment_hit.document_title == "Guide"
        assert deployment_hit.document_kind == "doc"
        assert deployment_hit.document_project == "proj"
    finally:
        store.close()


def test_reindex_populates_chunk_summary(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(
            project_dir / "guide.md",
            "# Guide\n\n"
            "This guide explains how the deployment pipeline orchestrates "
            "kubernetes manifests across staging and production environments.\n",
        )
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:guide.md", project="proj", path="guide.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)

        stored_chunks = store.get_chunks("proj:guide.md")
        assert stored_chunks, "expected at least one persisted chunk"
        assert any(
            chunk.summary is not None and "deployment pipeline" in chunk.summary.lower()
            for chunk in stored_chunks
        )
    finally:
        store.close()


def test_reindex_uses_api_spec_chunker_for_api_spec_kind(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        body = (
            "# Billing API\n\nIntro paragraph.\n\n"
            "## GET /v1/foo\n\nfoo endpoint.\n\n"
            "## POST /v1/bar\n\nbar endpoint.\n\n"
            "## Schemas\n\nschema body.\n"
        )
        _write(project_dir / "openapi.md", body)
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(
                _entry(
                    id="proj:openapi.md",
                    project="proj",
                    path="openapi.md",
                    kind="api-spec",
                ),
            )
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)

        assert len(store.upsert_chunks_calls) == 1
        chunked_id, chunks = store.upsert_chunks_calls[0]
        assert chunked_id == "proj:openapi.md"
        slugs = [chunk.slug for chunk in chunks]
        assert "_preamble" in slugs
        assert "get-v1-foo" in slugs
        assert "post-v1-bar" in slugs
        assert "_schemas" in slugs
    finally:
        store.close()


def test_entry_removed_clears_chunks(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "a.md", "# A\n\ncontent-a\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann_full = AnnotationsConfig(
            documents=(_entry(id="proj:a.md", project="proj", path="a.md"),)
        )
        ann_empty = AnnotationsConfig(documents=())

        reindex(config=cfg, annotations=ann_full, store=store, store_dir=store_dir, now=_frozen_now)
        assert store.get_chunks("proj:a.md") != []

        result = reindex(
            config=cfg, annotations=ann_empty, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.deleted == ("proj:a.md",)
        assert "proj:a.md" in store.delete_calls
        assert store.get_chunks("proj:a.md") == []
    finally:
        store.close()


def test_reindex_without_enricher_does_not_enrich(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "guide.md", "# Guide\n\nDeployment uses kubernetes.\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:guide.md", project="proj", path="guide.md"),)
        )

        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )

        # No enricher and an empty cache: no Claude calls, nothing written.
        assert result.enriched_chunks == 0
        assert result.enrichment_cache_hits == 0
        assert store.upsert_chunk_enrichment_calls == []
        assert store.enrichment_cache_put_calls == []
    finally:
        store.close()


def test_reindex_with_enricher_populates_chunks_and_fts(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "guide.md", "# Guide\n\nDeployment uses kubernetes manifests.\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:guide.md", project="proj", path="guide.md"),)
        )
        enricher = _StubEnricher(
            keywords=("вебхук", "webhook"),
            questions=("Как устроен вебхук?",),
            alt_phrasings=("event delivery",),
        )

        result = reindex(
            config=cfg,
            annotations=ann,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=enricher,
        )

        assert result.enriched_chunks == len(enricher.calls)
        assert result.enriched_chunks >= 1
        assert result.enrichment_cache_hits == 0
        assert len(store.upsert_chunk_enrichment_calls) == result.enriched_chunks
        assert len(store.enrichment_cache_put_calls) == result.enriched_chunks

        stored_chunks = store.get_chunks("proj:guide.md")
        assert stored_chunks
        assert all(
            chunk.enrichment is not None and "вебхук" in chunk.enrichment for chunk in stored_chunks
        )

        hits = store.query("вебхук")
        assert any(h.document_id == "proj:guide.md" for h in hits)
    finally:
        store.close()


def test_reindex_with_enricher_hits_cache_for_identical_chunk_in_new_doc(
    tmp_path: Path,
) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        body = "# Shared\n\nDeployment uses kubernetes manifests.\n"
        _write(project_dir / "a.md", body)
        cfg = _make_config(_make_project("proj", project_dir))
        ann_first = AnnotationsConfig(
            documents=(_entry(id="proj:a.md", project="proj", path="a.md"),)
        )
        enricher = _StubEnricher()

        first = reindex(
            config=cfg,
            annotations=ann_first,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=enricher,
        )
        first_call_count = len(enricher.calls)
        assert first.enriched_chunks == first_call_count
        assert first.enrichment_cache_hits == 0

        _write(project_dir / "b.md", body)
        ann_second = AnnotationsConfig(
            documents=(
                _entry(id="proj:a.md", project="proj", path="a.md"),
                _entry(id="proj:b.md", project="proj", path="b.md"),
            )
        )

        second = reindex(
            config=cfg,
            annotations=ann_second,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=enricher,
        )

        assert len(enricher.calls) == first_call_count
        assert second.enriched_chunks == 0
        assert second.enrichment_cache_hits == first_call_count
    finally:
        store.close()


def test_reindex_with_enricher_enriches_only_changed_chunk(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "guide.md"
        # Long preamble (>500 bytes) so the chunker doesn't auto-extend the
        # preamble into the section bodies; this keeps each section's chunk
        # content_hash independent of changes in other sections.
        preamble = (
            "This guide explains the team's deployment process and recovery "
            "procedures across staging and production environments in great "
            "detail. It is intended for engineers performing release work and "
            "oncall shifts and assumes familiarity with the existing pipeline. "
            "Sections below cover Section A (build) and Section B (deploy) in "
            "turn, with concrete examples and runbook references that the "
            "reader can follow step by step during an incident or release. "
            "Refer to the team's onboarding guide for context on terminology "
            "and the change-management workflow used by reviewers.\n\n"
            "Section index follows below.\n\n"
        )
        assert len(preamble.encode("utf-8")) >= 500
        body_v1 = (
            preamble
            + "## Section A\n\nAlpha section text body alpha.\n\n"
            + "## Section B\n\nBeta section text body beta.\n"
        )
        _write(md, body_v1)
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:guide.md", project="proj", path="guide.md"),)
        )
        enricher = _StubEnricher()

        first = reindex(
            config=cfg,
            annotations=ann,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=enricher,
        )
        first_calls = len(enricher.calls)
        assert first.enriched_chunks == first_calls
        assert first_calls >= 2

        body_v2 = (
            preamble
            + "## Section A\n\nAlpha section text body alpha.\n\n"
            + "## Section B\n\nBeta section text body beta WAS CHANGED.\n"
        )
        _write(md, body_v2)
        second = reindex(
            config=cfg,
            annotations=ann,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=enricher,
        )

        new_calls = len(enricher.calls) - first_calls
        assert new_calls == 1
        assert second.enriched_chunks == 1
        assert second.enrichment_cache_hits == first_calls - 1
    finally:
        store.close()


class _FailingEnricher:
    """Mimics ClaudeEnricher's failure mode: empty EnrichmentResult on every call."""

    def __init__(self, model: str = "claude-haiku-4-5") -> None:
        self.model = model
        self.calls: list[dict[str, object]] = []

    def enrich_chunk(
        self,
        *,
        title: str,
        heading_path: tuple[str, ...],
        body: str,
        summary: str | None,
    ) -> EnrichmentResult:
        self.calls.append(
            {
                "title": title,
                "heading_path": heading_path,
                "body": body,
                "summary": summary,
            }
        )
        return EnrichmentResult(
            keywords=(),
            questions=(),
            alt_phrasings=(),
            model=self.model,
            generated_at="2026-05-05T12:30:00+00:00",
        )


def test_reindex_does_not_cache_failed_enrichment(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        body = "# Guide\n\nDeployment uses kubernetes manifests.\n"
        _write(project_dir / "a.md", body)
        cfg = _make_config(_make_project("proj", project_dir))
        ann_first = AnnotationsConfig(
            documents=(_entry(id="proj:a.md", project="proj", path="a.md"),)
        )
        failing = _FailingEnricher()

        first = reindex(
            config=cfg,
            annotations=ann_first,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=failing,
        )

        assert first.enriched_chunks == 0
        assert first.enrichment_cache_hits == 0
        assert len(failing.calls) >= 1
        # Failed enrichment must not be cached or written to chunks/FTS;
        # otherwise a transient Claude error permanently disables enrichment
        # for every chunk with the same content_hash.
        assert store.enrichment_cache_put_calls == []
        assert store.upsert_chunk_enrichment_calls == []

        # When a different document with identical chunk content is added,
        # enrichment is retried (the failed result was not cached against
        # that content_hash).
        _write(project_dir / "b.md", body)
        ann_second = AnnotationsConfig(
            documents=(
                _entry(id="proj:a.md", project="proj", path="a.md"),
                _entry(id="proj:b.md", project="proj", path="b.md"),
            )
        )
        good = _StubEnricher(keywords=("вебхук",))

        second = reindex(
            config=cfg,
            annotations=ann_second,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=good,
        )

        assert second.enriched_chunks >= 1
        assert second.enrichment_cache_hits == 0
        assert len(good.calls) >= 1
        hits = store.query("вебхук")
        assert any(h.document_id == "proj:b.md" for h in hits)
    finally:
        store.close()


def test_reindex_skips_cache_when_model_differs(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        body = "# Guide\n\nDeployment uses kubernetes manifests.\n"
        _write(project_dir / "a.md", body)
        cfg = _make_config(_make_project("proj", project_dir))
        ann_first = AnnotationsConfig(
            documents=(_entry(id="proj:a.md", project="proj", path="a.md"),)
        )
        haiku = _StubEnricher(model="claude-haiku-4-5", keywords=("вебхукхаики",))

        first = reindex(
            config=cfg,
            annotations=ann_first,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=haiku,
        )
        assert first.enriched_chunks >= 1

        # Add a second doc with identical chunk content, but enrich with a
        # different model: cache hits would silently return the haiku output,
        # so the engine must treat the model mismatch as a miss and call
        # the new enricher.
        _write(project_dir / "b.md", body)
        ann_second = AnnotationsConfig(
            documents=(
                _entry(id="proj:a.md", project="proj", path="a.md"),
                _entry(id="proj:b.md", project="proj", path="b.md"),
            )
        )
        sonnet = _StubEnricher(model="claude-sonnet-4-6", keywords=("вебхуксоннет",))

        second = reindex(
            config=cfg,
            annotations=ann_second,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=sonnet,
        )

        assert len(sonnet.calls) >= 1
        assert second.enriched_chunks >= 1
        assert second.enrichment_cache_hits == 0
        # The new model's keyword should be searchable on the new doc.
        sonnet_hits = store.query("вебхуксоннет")
        assert any(h.document_id == "proj:b.md" for h in sonnet_hits)
    finally:
        store.close()


def test_no_enrichment_repopulates_chunks_from_cache(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        chunk_body = "## Webhooks\n\nDeployment uses kubernetes manifests.\n"
        original = "# Guide\n\nintro paragraph.\n\n" + chunk_body
        _write(project_dir / "guide.md", original)
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:guide.md", project="proj", path="guide.md"),)
        )
        good = _StubEnricher(keywords=("вебхуккеш",))

        reindex(
            config=cfg,
            annotations=ann,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=good,
        )
        baseline_calls = len(good.calls)
        assert baseline_calls >= 2  # intro chunk + webhooks chunk

        # Edit only the intro paragraph — the "Webhooks" chunk's content_hash
        # is unchanged but the doc body changed, so chunks are rebuilt and
        # `upsert_chunks` clears all enrichment columns to NULL. With
        # --no-enrichment we cannot call Claude, but the engine should still
        # consult the cache and restore the unchanged chunk's enrichment.
        edited = "# Guide\n\nrewritten intro paragraph.\n\n" + chunk_body
        _write(project_dir / "guide.md", edited)

        result = reindex(
            config=cfg,
            annotations=ann,
            store=store,
            store_dir=store_dir,
            now=_frozen_now,
            enricher=None,
        )

        # No new Claude calls happened (enricher is None) yet the unchanged
        # chunk's enrichment was restored from the cache.
        assert result.enriched_chunks == 0
        assert result.enrichment_cache_hits >= 1
        cached_hits = store.query("вебхуккеш")
        assert any(h.document_id == "proj:guide.md" for h in cached_hits)
    finally:
        store.close()


def test_reindex_extracts_mentions_for_inserted_doc(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        body = (
            "# Guide\n\n"
            "Reference src/foo.py:42 and `BillingEntity`.\n"
            "Call GET /api/v1/users for the list. See [link](other.md).\n"
        )
        _write(project_dir / "guide.md", body)
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:guide.md", project="proj", path="guide.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)

        chunks = store.get_chunks("proj:guide.md")
        assert chunks
        first_chunk_id = chunks[0].id
        mentions = store.get_mentions(first_chunk_id)
        assert Mention(target="src/foo.py", target_kind="code", line_range="42") in mentions
        assert Mention(target="GET /api/v1/users", target_kind="endpoint") in mentions
        assert Mention(target="BillingEntity", target_kind="schema") in mentions
        assert Mention(target="other.md", target_kind="doc") in mentions
        assert any(call[0] == first_chunk_id for call in store.upsert_mentions_calls)
    finally:
        store.close()


def test_reindex_replaces_mentions_when_body_edited(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "guide.md"
        _write(
            md,
            "# Guide\n\nReference src/foo.py and call GET /api/v1/users.\n",
        )
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:guide.md", project="proj", path="guide.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)
        first_chunk_id = store.get_chunks("proj:guide.md")[0].id
        before = store.get_mentions(first_chunk_id)
        assert any(m.target == "GET /api/v1/users" for m in before)

        _write(md, "# Guide\n\nReference src/foo.py only.\n")
        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)

        after = store.get_mentions(first_chunk_id)
        assert any(m.target == "src/foo.py" for m in after)
        assert all(m.target != "GET /api/v1/users" for m in after)
    finally:
        store.close()


def test_reindex_does_not_re_extract_mentions_on_metadata_only_update(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        md = project_dir / "doc.md"
        _write(
            md,
            "---\nkind: pattern\n---\n# Title\n\nReference src/foo.py here.\n",
        )
        cfg = _make_config(_make_project("proj", project_dir))
        ann = AnnotationsConfig(
            documents=(_entry(id="proj:doc.md", project="proj", path="doc.md"),)
        )

        reindex(config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now)
        first_chunk_id = store.get_chunks("proj:doc.md")[0].id
        baseline = store.get_mentions(first_chunk_id)
        assert any(m.target == "src/foo.py" for m in baseline)
        upsert_mentions_calls_first = list(store.upsert_mentions_calls)

        _write(md, "---\nkind: service\n---\n# Title\n\nReference src/foo.py here.\n")
        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.updated_metadata_only == ("proj:doc.md",)
        assert result.updated_content == ()
        # No new upsert_mentions calls because chunks were not rebuilt.
        assert store.upsert_mentions_calls == upsert_mentions_calls_first
        assert store.get_mentions(first_chunk_id) == baseline
    finally:
        store.close()


def test_reindex_deletes_mentions_when_doc_removed(tmp_path: Path) -> None:
    store_dir, project_dir, _db, store = _setup(tmp_path)
    try:
        _write(project_dir / "a.md", "# A\n\nSee src/foo.py for details.\n")
        cfg = _make_config(_make_project("proj", project_dir))
        ann_full = AnnotationsConfig(
            documents=(_entry(id="proj:a.md", project="proj", path="a.md"),)
        )
        ann_empty = AnnotationsConfig(documents=())

        reindex(config=cfg, annotations=ann_full, store=store, store_dir=store_dir, now=_frozen_now)
        first_chunk_id = store.get_chunks("proj:a.md")[0].id
        assert store.get_mentions(first_chunk_id), "expected mentions before delete"

        reindex(
            config=cfg, annotations=ann_empty, store=store, store_dir=store_dir, now=_frozen_now
        )

        # Document is gone, chunks cascaded, mentions cascaded.
        assert store.get_chunks("proj:a.md") == []
        assert store.get_mentions(first_chunk_id) == []
        assert store.find_backlinks("src/foo.py") == []
    finally:
        store.close()
