"""SHA256 content_hash, frontmatter_hash, and annotation_hash over canonical JSON."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def content_hash(body: str) -> str:
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def frontmatter_hash(frontmatter_text: str) -> str:
    return hashlib.sha256(frontmatter_text.encode("utf-8")).hexdigest()


def annotation_hash(merged: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(merged),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
