"""Unit tests for the reindex engine."""

from __future__ import annotations

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
from cadence_memory.reindex.engine import ReindexError, reindex
from cadence_memory.store.interface import StoredDocument
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

    def query(
        self,
        text: str,
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> list[StoredDocument]:
        return self.inner.query(text, kind=kind, project=project, limit=limit)

    def all_ids(self) -> set[str]:
        return self.inner.all_ids()

    def close(self) -> None:
        self.inner.close()


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

        _write(md, "# Title\n\nnew body content\n")
        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.updated_content == ("proj:README.md",)
        assert result.inserted == ()
        assert len(store.upsert_calls) == upsert_count_after_first + 1
        got = store.get("proj:README.md")
        assert got is not None
        assert got.body == "# Title\n\nnew body content\n"

        hits = store.query("body content")
        assert any(h.id == "proj:README.md" for h in hits)
        old_hits = store.query("old")
        assert all(h.id != "proj:README.md" for h in old_hits)
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

        _write(md, "---\nkind: service\n---\n# Title\n\nbody\n")
        result = reindex(
            config=cfg, annotations=ann, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.updated_metadata_only == ("proj:doc.md",)
        assert result.updated_content == ()
        second_doc = store.upsert_calls[-1]
        assert second_doc.body == first_doc.body
        assert second_doc.kind == "service"
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
        result = reindex(
            config=cfg, annotations=ann_v2, store=store, store_dir=store_dir, now=_frozen_now
        )

        assert result.updated_metadata_only == ("proj:doc.md",)
        assert result.updated_content == ()
        got = store.get("proj:doc.md")
        assert got is not None
        assert got.tags == ("a", "b")
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
