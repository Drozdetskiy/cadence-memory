# cadence-memory

> **Status: rewrite in progress.** The v1 codebase has been wiped. v2 is a Karpathy-style LLM-maintained wiki: a worker walks new commits in tracked source repos and asks Claude to keep a single master wiki current. Search via [`qmd`](https://github.com/tobi/qmd) MCP. No SQLite/FTS5, no per-project chunking, no retrieval tuning.
>
> See [`docs/design2.md`](docs/design2.md) for the design and [`docs/features.md`](docs/features.md) for the implementation order. There is **no installable package** until task `0001-package-skeleton` lands.

## What it is, in one paragraph

An LLM-authored knowledge base for Claude Code. You point cadence-memory at a list of git repositories and a master-wiki repo. A background worker clones each repo, walks its history one commit at a time, and asks Claude to update the wiki — entity pages, architecture overviews, ADRs, an `index.md`, a `log.md`. After catching up, the worker pulls each repo's tracked branch periodically and continues to extend the wiki as new commits land. You read the wiki in any markdown editor; Claude reads it via a SessionStart hook and via the [`qmd`](https://github.com/tobi/qmd) search MCP. The wiki is the artifact, not an SQLite index.

## Why a rewrite

The v1 design was a chunked SQLite/FTS5 retrieval pipeline (RAG) with progressively layered tuning — identifier boost, query expansion, Claude rerank, per-chunk enrichment. It worked but the unit of value was a hit list of fragments. v2 follows [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) idea: knowledge is **compiled on ingest** into a structured, interlinked wiki, not re-derived on every query. The wiki gets richer as more commits land; queries hit synthesized pages, not raw chunks. Search becomes navigation, not the answer engine.

## Inspiration

- [Andrej Karpathy — LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — the core idea.
- [`docs/article-about-db.md`](docs/article-about-db.md) — extended workflow (per-project + master wiki, post-commit hooks, qmd MCP, scheduled lint).

## Layout (post-rewrite)

```
cadence-memory/
  docs/
    design2.md                 # the design — read this first
    features.md                # implementation order + cadence init template
    article-about-db.md        # reference
  pyproject.toml               # stub until task 0001
  Makefile                     # stub until task 0001
  CLAUDE.md                    # branch/commit conventions, release flow
  README.md                    # this file
  LICENSE
```

`src/` and `tests/` are intentionally absent. They will be reintroduced by `0001-package-skeleton` and built up across the tasks listed in `docs/features.md` §2.

## License

MIT — see [LICENSE](LICENSE).
