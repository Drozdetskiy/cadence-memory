"""Table output formatter for `--format table` using manual column and textwrap layout."""

from __future__ import annotations

from cadence_memory.store.interface import StoredDocument

__all__ = ["format_document", "format_documents"]

_TITLE_MAX = 60
_COLUMNS: tuple[str, ...] = ("id", "kind", "project", "title", "tags")


def _truncate_title(title: str, *, limit: int = _TITLE_MAX) -> str:
    if len(title) <= limit:
        return title
    return title[: limit - 1] + "…"


def _row(doc: StoredDocument) -> tuple[str, str, str, str, str]:
    return (
        doc.id,
        doc.kind,
        doc.project or "",
        _truncate_title(doc.title),
        ", ".join(doc.tags),
    )


def format_documents(docs: list[StoredDocument]) -> str:
    if not docs:
        return "(no documents)"

    rows: list[tuple[str, ...]] = [_COLUMNS]
    rows.extend(_row(doc) for doc in docs)

    widths = [max(len(row[col_idx]) for row in rows) for col_idx in range(len(_COLUMNS))]
    lines = [
        "  ".join(value.ljust(widths[col_idx]) for col_idx, value in enumerate(row)).rstrip()
        for row in rows
    ]
    return "\n".join(lines)


def format_document(doc: StoredDocument, *, include_body: bool = True) -> str:
    fields: tuple[tuple[str, str], ...] = (
        ("id", doc.id),
        ("kind", doc.kind),
        ("project", doc.project or ""),
        ("title", doc.title),
        ("tags", ", ".join(doc.tags)),
        ("related", ", ".join(doc.related)),
        ("content_hash", doc.content_hash),
        ("frontmatter_hash", doc.frontmatter_hash),
        ("annotation_hash", doc.annotation_hash),
        ("indexed_at", doc.indexed_at),
    )
    label_width = max(len(label) for label, _ in fields)
    header = "\n".join(f"{label.ljust(label_width)}  {value}" for label, value in fields)
    if not include_body:
        return header
    return f"{header}\n\n---\n{doc.body}"
