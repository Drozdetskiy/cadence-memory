# Changelog

## v0.1.0 - 2026-05-05

First public release. Available via `pip install cadence-memory` and `brew tap Drozdetskiy/cadence && brew install drozdetskiy/cadence/cadence-memory`.

### New Features

- `cadence-memory init` bootstraps a memory store (config.yaml, annotations-config.yaml, .gitignore, ephemeral/, empty SQLite index, `git init`).
- `cadence-memory reindex` rebuilds the SQLite/FTS5 index from `config.yaml` and `annotations-config.yaml`; `--verbose` lists affected document ids.
- `cadence-memory status` is a dry-run reindex that reports what would change without writing.
- `cadence-memory list` / `query` / `get` / `show` browse and full-text-search indexed documents with table or json output.
- `cadence-memory ephemeral add/list/remove/clear` manages per-task notes by copy, symlink, or stdin inline.
- `cadence-memory discover` runs Claude Code over project markdown to populate `annotations-config.yaml`; writes `.proposed` by default, overwrites in place with `--apply` (refuses when the file has uncommitted changes).
- `cadence-memory chat` launches an interactive Claude session with `CADENCE_MEMORY_DIR` exported so the read-only `cadence-memory` skill can query the store.
- Active store is resolved via `--store`, `CADENCE_MEMORY_DIR`, or by walking up from the current directory to find `config.yaml` next to `index.sqlite`.
- Ships two Claude Code skills as embedded defaults: read-only `cadence-memory` (used by `chat`) and write-side `cadence-memory-discover` (used by `discover`).
