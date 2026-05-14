# Changelog

## v0.4.4 — unreleased

### New
- Every command (`bootstrap`, `worker run`, `worker daemon`, `ingest`, `lint`) now streams progress events to stdout in real time: timestamped phase/stage/ingest start and end events, plus tool-call and signal markers from the Claude subprocess.
- Optional structured JSONL sink: set `progress.jsonl: true` in config to append one JSON record per event to `progress.jsonl_path`.
- New global CLI flags: `--verbose` / `-v` (level=debug), `--quiet` / `-q` (level=warn), `--no-color` (plain ASCII output).

### Fixes
- Project skills scaffolded by `cadence-memory init` are now written as `.claude/skills/<name>/SKILL.md` instead of flat `.claude/skills/<name>.md`; Claude Code only discovers the directory layout, so the previously scaffolded skills never loaded.

### Changed
- The YAML loader now warns on unknown keys instead of raising `ConfigError`. Malformed values on *known* keys still error. This makes schema removals non-breaking and surfaces typos as warnings.

### Removed
- `RepoConfig.exclude` and `Config.raw_auto_ingest`. Neither was ever read at runtime.

## v0.4.3 — 2026-05-14

### Fixes
- `cadence-memory` no longer passes `--bare` to the `claude` subprocess; this was breaking authentication on Claude Code 2.1.x.
- The Claude subprocess now inherits the full parent environment (only `CLAUDECODE` is stripped), so CI credentials and Claude Code session variables pass through automatically.
- After a failed Claude run, the wiki revert only undoes the delta Claude introduced — pre-existing user edits in the working tree are preserved.

### Changed
- `--max-budget-usd` is no longer passed to the `claude` subprocess. The `budget_usd` config key is now ignored and will be removed in a future release.

## v0.4.2 — 2026-05-13

### Fixes
- Bootstrap worker passes `--verbose` to the Claude subprocess so Claude Code 2.1.x accepts `-p` + `--output-format stream-json` without erroring.

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
