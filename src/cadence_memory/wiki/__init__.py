"""Wiki repo locator and re-exports."""

from cadence_memory.wiki.locator import WikiNotFoundError, resolve_wiki_dir

__all__ = [
    "WikiNotFoundError",
    "resolve_wiki_dir",
]
