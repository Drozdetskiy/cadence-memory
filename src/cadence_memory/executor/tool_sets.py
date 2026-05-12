"""Named allowed-tool sets for Claude runner callers."""

WIKI_READWRITE: tuple[str, ...] = ("Read", "Write", "Edit", "Glob", "Grep")
WIKI_READONLY: tuple[str, ...] = ("Read", "Glob", "Grep")

__all__ = ["WIKI_READONLY", "WIKI_READWRITE"]
