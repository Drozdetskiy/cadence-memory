"""Tests for the diff-budget guard (design2 §7)."""

from __future__ import annotations

from pathlib import Path

from cadence_memory.worker.diff_budget import maybe_shard_diff, shard_noise_batch

FIXTURES = Path(__file__).parent / "fixtures"


def _file_block(path: str, payload_bytes: int) -> str:
    """Synthesize a single `diff --git` block for `path` with roughly `payload_bytes` of body."""
    body = "+x" * (payload_bytes // 2)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index e69de29..4b825dc 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,1 @@\n"
        f"{body}\n"
    )


def test_under_budget_returns_single_shard() -> None:
    diff = (FIXTURES / "sample_diff_small.txt").read_text(encoding="utf-8")
    result = maybe_shard_diff(diff)
    assert len(result) == 1
    assert result[0] == diff


def test_over_budget_splits_by_top_dir() -> None:
    # 60_000 tokens ≈ 240_000 bytes; per-block payload 130_000 chars each → ≥ budget+1.
    src_block = _file_block("src/big.py", 130_000)
    tests_block = _file_block("tests/big.py", 130_000)
    diff = src_block + tests_block

    result = maybe_shard_diff(diff)

    assert len(result) == 2
    for shard in result:
        assert shard.startswith("diff --git ")
    assert result[0] == src_block
    assert result[1] == tests_block


def test_over_budget_unsplittable_returns_single() -> None:
    big_block = _file_block("src/only.py", 260_000)
    result = maybe_shard_diff(big_block)
    assert result == (big_block,)


def test_empty_diff_returns_single_empty() -> None:
    assert maybe_shard_diff("") == ("",)


def test_file_with_no_directory_grouped_under_root() -> None:
    root_block = _file_block("README.md", 130_000)
    src_block = _file_block("src/big.py", 130_000)
    diff = root_block + src_block

    result = maybe_shard_diff(diff)

    assert len(result) == 2
    assert result[0] == root_block
    assert result[1] == src_block


def test_custom_max_tokens_triggers_sharding() -> None:
    src_block = _file_block("src/a.py", 200)
    tests_block = _file_block("tests/a.py", 200)
    diff = src_block + tests_block

    # Default budget keeps it whole; a tiny budget forces a split.
    assert maybe_shard_diff(diff) == (diff,)
    assert maybe_shard_diff(diff, max_tokens=10) == (src_block, tests_block)


def test_shard_noise_batch_combines_under_budget() -> None:
    per_commit = (
        "# Commit aaaaaaa — first\ndiff --git a/x b/x\n+a\n",
        "# Commit bbbbbbb — second\ndiff --git a/y b/y\n+b\n",
    )
    result = shard_noise_batch(per_commit)
    assert result == ("\n".join(per_commit),)


def test_shard_noise_batch_splits_over_budget() -> None:
    big_a = "# Commit aaaaaaa — first\n" + _file_block("src/a.py", 130_000)
    big_b = "# Commit bbbbbbb — second\n" + _file_block("tests/b.py", 130_000)
    result = shard_noise_batch((big_a, big_b))
    assert result == (big_a, big_b)
