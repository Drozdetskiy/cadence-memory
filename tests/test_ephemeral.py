"""Tests for the ephemeral document module: copy/symlink/inline add and lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from cadence_memory import ephemeral
from cadence_memory.ephemeral import EphemeralAddOptions, EphemeralExists
from cadence_memory.store.sqlite_store import SqliteStore


def _make_store(tmp_path: Path) -> tuple[Path, SqliteStore]:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "ephemeral").mkdir()
    store = SqliteStore(store_dir / "index.sqlite")
    return store_dir, store


def test_add_copy_mode_persists_file_and_row(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "source.md"
    src.write_text("# Source\n\ncopied body\n", encoding="utf-8")

    doc = ephemeral.add(
        EphemeralAddOptions(source=src),
        store=store,
        store_dir=store_dir,
    )

    target = store_dir / "ephemeral" / "source.md"
    assert target.is_file()
    assert not target.is_symlink()
    assert doc.id == "eph:source"
    assert doc.source_type == "ephemeral"
    assert doc.project is None
    assert doc.rel_path == "ephemeral/source.md"
    assert doc.body == "# Source\n\ncopied body\n"
    assert doc.title == "Source"
    assert doc.kind == "task"
    assert doc.content_hash
    assert doc.frontmatter_hash
    assert doc.annotation_hash

    fetched = store.get("eph:source")
    assert fetched is not None
    assert fetched.body == doc.body
    store.close()


def test_add_symlink_mode_links_and_remove_keeps_source(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "linked.md"
    src.write_text("# Linked\n\nlinked body\n", encoding="utf-8")

    doc = ephemeral.add(
        EphemeralAddOptions(source=src, use_symlink=True),
        store=store,
        store_dir=store_dir,
    )
    target = store_dir / "ephemeral" / "linked.md"
    assert target.is_symlink()
    assert target.resolve() == src.resolve()
    assert doc.id == "eph:linked"

    ephemeral.remove("eph:linked", store=store, store_dir=store_dir)
    assert not target.exists()
    assert not target.is_symlink()
    assert src.is_file()
    assert src.read_text(encoding="utf-8") == "# Linked\n\nlinked body\n"
    assert store.get("eph:linked") is None
    store.close()


def test_add_inline_writes_text_and_persists(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)

    doc = ephemeral.add(
        EphemeralAddOptions(
            eph_id="mynote",
            inline_text="# Inline\n\ninline body\n",
            tags=("a", "b"),
        ),
        store=store,
        store_dir=store_dir,
    )

    target = store_dir / "ephemeral" / "mynote.md"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "# Inline\n\ninline body\n"
    assert doc.id == "eph:mynote"
    assert doc.tags == ("a", "b")
    assert doc.kind == "task"

    fetched = store.get("eph:mynote")
    assert fetched is not None
    assert fetched.title == "Inline"
    store.close()


def test_add_inline_without_id_raises(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)

    with pytest.raises(ValueError, match="explicit ephemeral id"):
        ephemeral.add(
            EphemeralAddOptions(inline_text="# Inline\n\nbody\n"),
            store=store,
            store_dir=store_dir,
        )
    store.close()


def test_add_without_source_or_inline_raises(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)

    with pytest.raises(ValueError, match="source path or inline_text"):
        ephemeral.add(
            EphemeralAddOptions(),
            store=store,
            store_dir=store_dir,
        )
    store.close()


def test_add_duplicate_id_raises(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "dup.md"
    src.write_text("# Dup\n\nbody\n", encoding="utf-8")

    ephemeral.add(EphemeralAddOptions(source=src), store=store, store_dir=store_dir)
    with pytest.raises(EphemeralExists) as exc:
        ephemeral.add(
            EphemeralAddOptions(source=src),
            store=store,
            store_dir=store_dir,
        )
    assert "eph:dup" in str(exc.value)
    store.close()


def test_add_explicit_id_with_unfriendly_filename(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "Some Weird FILE!!.md"
    src.write_text("# W\n\nbody\n", encoding="utf-8")

    doc = ephemeral.add(
        EphemeralAddOptions(source=src, eph_id="cleanid"),
        store=store,
        store_dir=store_dir,
    )
    assert doc.id == "eph:cleanid"
    assert (store_dir / "ephemeral" / "cleanid.md").is_file()
    store.close()


def test_remove_is_idempotent(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "rm.md"
    src.write_text("# Rm\n\nbody\n", encoding="utf-8")

    ephemeral.add(EphemeralAddOptions(source=src), store=store, store_dir=store_dir)
    target = store_dir / "ephemeral" / "rm.md"
    assert target.is_file()

    ephemeral.remove("eph:rm", store=store, store_dir=store_dir)
    assert not target.exists()
    assert store.get("eph:rm") is None

    ephemeral.remove("eph:rm", store=store, store_dir=store_dir)
    assert store.get("eph:rm") is None
    store.close()


def test_clear_removes_all_and_preserves_gitkeep(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    gitkeep = store_dir / "ephemeral" / ".gitkeep"
    gitkeep.write_text("", encoding="utf-8")

    for stem in ("a", "b", "c"):
        src = tmp_path / f"{stem}.md"
        src.write_text(f"# {stem}\n\nbody\n", encoding="utf-8")
        ephemeral.add(EphemeralAddOptions(source=src), store=store, store_dir=store_dir)

    assert len(ephemeral.list_(store)) == 3

    count = ephemeral.clear(store=store, store_dir=store_dir)
    assert count == 3
    assert ephemeral.list_(store) == []
    assert gitkeep.is_file()
    remaining = sorted(p.name for p in (store_dir / "ephemeral").iterdir())
    assert remaining == [".gitkeep"]
    store.close()


def test_list_returns_only_ephemeral(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "only.md"
    src.write_text("# Only\n\nbody\n", encoding="utf-8")
    ephemeral.add(EphemeralAddOptions(source=src), store=store, store_dir=store_dir)

    docs = ephemeral.list_(store)
    assert len(docs) == 1
    assert docs[0].id == "eph:only"
    assert docs[0].source_type == "ephemeral"
    store.close()


def test_remove_rejects_non_ephemeral_id(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "alpha.md"
    src.write_text("# Alpha\n\nbody\n", encoding="utf-8")
    ephemeral.add(
        EphemeralAddOptions(source=src, eph_id="alpha"),
        store=store,
        store_dir=store_dir,
    )

    sentinel_doc = store.get("eph:alpha")
    assert sentinel_doc is not None
    forged_row = sentinel_doc.__class__(
        id="proj:alpha.md",
        source_type="project",
        project="proj",
        abs_path=sentinel_doc.abs_path,
        rel_path="alpha.md",
        kind=sentinel_doc.kind,
        title=sentinel_doc.title,
        body=sentinel_doc.body,
        tags=sentinel_doc.tags,
        related=sentinel_doc.related,
        content_hash=sentinel_doc.content_hash,
        frontmatter_hash=sentinel_doc.frontmatter_hash,
        annotation_hash=sentinel_doc.annotation_hash,
        mtime=sentinel_doc.mtime,
        indexed_at=sentinel_doc.indexed_at,
    )
    store.upsert(forged_row)

    with pytest.raises(ValueError, match="not an ephemeral document id"):
        ephemeral.remove("proj:alpha.md", store=store, store_dir=store_dir)
    assert store.get("proj:alpha.md") is not None

    with pytest.raises(ValueError, match="not an ephemeral document id"):
        ephemeral.remove(":global.md", store=store, store_dir=store_dir)

    with pytest.raises(ValueError):
        ephemeral.remove("eph:bad/name", store=store, store_dir=store_dir)
    store.close()


def test_slugify_invalid_raises(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "!!!.md"
    src.write_text("body\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot derive slug"):
        ephemeral.add(EphemeralAddOptions(source=src), store=store, store_dir=store_dir)
    store.close()


def test_add_api_spec_kind_routes_chunker(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "spec.md"
    src.write_text(
        "# Spec\n\nIntro.\n\n## GET /v1/foo\n\nfoo endpoint.\n\n## Schemas\n\nschema body.\n",
        encoding="utf-8",
    )

    ephemeral.add(
        EphemeralAddOptions(source=src, kind="api-spec"),
        store=store,
        store_dir=store_dir,
    )

    chunks = store.get_chunks("eph:spec")
    slugs = [chunk.slug for chunk in chunks]
    assert "_preamble" in slugs
    assert "get-v1-foo" in slugs
    assert "_schemas" in slugs
    store.close()


def test_add_makes_ephemeral_searchable(tmp_path: Path) -> None:
    store_dir, store = _make_store(tmp_path)
    src = tmp_path / "searchme.md"
    src.write_text(
        "# Searchable\n\nbody contains the unique word zephyranthes.\n",
        encoding="utf-8",
    )

    ephemeral.add(EphemeralAddOptions(source=src), store=store, store_dir=store_dir)

    hits = store.query("zephyranthes")
    assert hits
    assert {c.document_id for c in hits} == {"eph:searchme"}
    store.close()
