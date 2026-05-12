from cadence_memory.executor.tool_sets import WIKI_READONLY, WIKI_READWRITE


def test_wiki_readwrite_is_frozen_tuple() -> None:
    assert isinstance(WIKI_READWRITE, tuple)
    assert WIKI_READWRITE == ("Read", "Write", "Edit", "Glob", "Grep")


def test_wiki_readonly_is_subset_of_readwrite() -> None:
    assert isinstance(WIKI_READONLY, tuple)
    assert WIKI_READONLY == ("Read", "Glob", "Grep")
    assert set(WIKI_READONLY).issubset(WIKI_READWRITE)
    assert "Write" not in WIKI_READONLY
    assert "Edit" not in WIKI_READONLY
