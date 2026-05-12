"""Diff-budget guard: shard oversize diffs by top-level directory (design2 §7)."""

from __future__ import annotations

_DIFF_HEADER = "diff --git "
_ROOT_GROUP = "<root>"


def _approx_tokens(text: str) -> int:
    return len(text) // 4


def _split_file_diffs(diff: str) -> list[str]:
    """Split a unified diff into per-file blocks keyed by `diff --git ` headers."""
    parts: list[str] = []
    idx = 0
    while idx < len(diff):
        next_idx = diff.find(_DIFF_HEADER, idx + len(_DIFF_HEADER))
        if next_idx == -1:
            parts.append(diff[idx:])
            break
        parts.append(diff[idx:next_idx])
        idx = next_idx
    return parts


def _top_dir(file_block: str) -> str:
    """Return the first path segment of the `a/` path in a `diff --git` block."""
    first_line, _, _ = file_block.partition("\n")
    if not first_line.startswith(_DIFF_HEADER):
        return _ROOT_GROUP
    tokens = first_line[len(_DIFF_HEADER) :].split()
    if not tokens:
        return _ROOT_GROUP
    a_path = tokens[0]
    if a_path.startswith("a/"):
        a_path = a_path[2:]
    if "/" not in a_path:
        return _ROOT_GROUP
    return a_path.split("/", 1)[0]


def maybe_shard_diff(diff: str, *, max_tokens: int = 60_000) -> tuple[str, ...]:
    """Return one shard if the diff fits the token budget, otherwise group by top-level dir.

    Token count is approximated as `len(diff) // 4` per design2 §7. When the
    diff exceeds the budget, file diffs are grouped by the first path segment
    of each `diff --git a/<path>` header (top-level files fall under
    `<root>`), preserving original order within a group. If a single group
    still exceeds the budget, it is returned as one oversize shard.
    """
    if _approx_tokens(diff) <= max_tokens:
        return (diff,)

    blocks = _split_file_diffs(diff)
    if len(blocks) <= 1:
        return (diff,)

    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for block in blocks:
        key = _top_dir(block) if block.startswith(_DIFF_HEADER) else _ROOT_GROUP
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(block)

    if len(order) <= 1:
        return (diff,)

    return tuple("".join(groups[key]) for key in order)


def shard_noise_batch(
    per_commit_diffs: tuple[str, ...], *, max_tokens: int = 60_000
) -> tuple[str, ...]:
    """Combine per-commit diffs into one shard if they fit, else one shard per commit.

    NoiseBatchEvent diffs carry inter-commit ``# Commit <sha> — <subject>``
    headers; feeding the joined string through ``maybe_shard_diff`` would
    misattribute those headers when it splits at ``diff --git`` boundaries.
    Sharding at the commit boundary keeps each shard's attribution intact.
    """
    combined = "\n".join(per_commit_diffs)
    if _approx_tokens(combined) <= max_tokens:
        return (combined,)
    return per_commit_diffs


__all__ = ["maybe_shard_diff", "shard_noise_batch"]
