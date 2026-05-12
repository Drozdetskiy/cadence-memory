# Changelog

## v0.4.1 — 2026-05-13

### Fixes
- Bootstrap worker passes `--verbose` to the Claude subprocess so Claude Code 2.1.x accepts `-p` + `--output-format stream-json` without erroring.

## v0.4.0 — 2026-05-12

Full rewrite. v2 is a Karpathy-style LLM-maintained wiki, not a chunked
SQLite/FTS5 retrieval pipeline. No migration path from v0.x — `init` a
fresh master-wiki repo and let the worker populate it.

### New
- `cadence-memory init` scaffolds a master-wiki repo (config, CLAUDE.md,
  skills, hooks, seed pages).
- `cadence-memory repos add/list/remove` manages tracked source repositories.
- `cadence-memory worker run` walks new commits since the last processed
  SHA and asks Claude to update the wiki — per-commit ingest, with budget
  caps and noise-batching.
- `cadence-memory worker daemon` runs the above on a configurable poll
  interval, SIGTERM-safe between repos.
- `cadence-memory bootstrap <repo>` runs a 5-stage initial pass for
  repos whose history is too long or noisy for commit-walking.
- `cadence-memory ingest <path>` integrates manual sources from `raw/notes/`.
- `cadence-memory lint` audits the wiki — orphan pages, broken wikilinks,
  contradictions, missing pages — writing fixes to a `lint/<date>` branch.
- `cadence-memory query <text>` searches the wiki via qmd (preferred) or
  ripgrep (fallback).
- `cadence-memory status [--short]` summarizes worker state; `--short` is
  the form consumed by the SessionStart hook.
- `cadence-memory hooks install` (re)installs the post-commit qmd-indexing
  hook in an existing wiki.
- Two Claude Code skills: `wiki-researcher` (read-side) and `wiki-ingest`
  (manual integration). SessionStart hook loads context every session.

### Deleted (vs v0.x)
- SQLite/FTS5 store, chunking, identifier boost, query expansion, Claude
  rerank, per-chunk enrichment, mentions/backlinks indexing.
- `cadence-memory reindex`, `discover`, `chat`, `ephemeral`, `mentions`,
  `backlinks`, `projects`, `get`. (Their roles were replaced by the
  wiki itself and the worker.)
