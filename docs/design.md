# cadence-memory — design document

Source: design discussion for an extension to [cadence](https://github.com/Drozdetskiy/cadence). This document is the result of working through the requirements and architectural decisions, ready to continue with in Claude Code.

---

## 1. Context and goal

### What cadence is
`cadence` is an autonomous task-execution pipeline on top of Claude Code. It runs Claude through a structured cycle: `plan → branch → iterative implementation → multi-agent code review → review-loop → optional finalize`. A thin orchestrator: Claude does the work, cadence keeps it on the rails (signals, retries, idle/session timeouts, break/resume, per-phase models, git integration).

Key phases:
- `cadence --plan <file>` — interactive Q&A with Claude, draft review, final plan in `<file>-plan.md`
- `cadence --task <plan>` — branch + `### Task N:` iterations with commits
- `cadence --review` — 4 parallel agents (quality, implementation, testing, simplification) + review-loop
- `cadence --finalize` — optional

Tech: Python 3.14+, Typer CLI, Protocol-based interfaces, mypy --strict, embedded prompts/agents in `src/cadence/defaults/`, override via `.cadence/prompts/` and `.cadence/agents/`. Signals of the form `<<<CADENCE:NAME>>>`.

### Goal of cadence-memory
A cross-project knowledge layer for Claude. So that during `cadence --plan` Claude understands:
- system topology (what services exist, how they're connected)
- domain model and glossary (shared terms)
- accepted patterns and ADRs (how we do auth, errors, migrations, observability)
- history of completed tasks (what cadence has already done in which services)
- per-service surface (API, events, DB schema, stack, owners)
- open questions

This makes it possible to draft complex plans that describe tasks spanning several subsystems at once.

---

## 2. Existing projects in the space (for reference)

Decision: **do not depend on third-party memory servers**, build our own on SQLite + markdown. The list below is purely for architectural inspiration.

| Project | Useful idea | Decision |
|---|---|---|
| **Basic Memory** | Most mature: markdown-first KG with MCP, hybrid search (FTS + vector), schema_infer/diff. Mobile/cloud, regular releases. | Don't take as a dependency. Reuse the markdown-first approach. |
| **AXME Code** | Closest in spirit: per-repo `.axme-code/`, workspace-level rules + repo-specific, background auditor that extracts memory from session transcripts. | Post-run auditor idea — on the roadmap, not in MVP. |
| **Serena** | Symbol-level semantic search via LSP. A complement, not a replacement — it provides IDE-level code context, we need an architectural one. | Could be plugged in separately as an optional MCP in Claude Code. |
| **mcp-knowledge-graph (AIM)** | JSONL storage, project-local + global stores, `_aim` safety markers. | Reuse the explicit safety-markers and dual-scope ideas conceptually. |
| **Anthropic Memory MCP (reference)** | Knowledge graph over entities/relations/observations. | Schema reference. |

**A note on GitHub stars:** the space is young (MCP was announced by Anthropic in November 2024); every project here has few stars — that's normal. Look at recent commits, issue response time, releases, tests — not stars. Cadence-memory also starts at 0 stars, which is fine.

---

## 3. Final cadence-memory model

### Core principle
**Markdown = source of truth, SQLite = derived index.**
- All knowledge lives in `.md` files with YAML frontmatter — human-readable, committed to git, greppable, editable in any editor
- SQLite is a cache for fast search (FTS5), filters, and the relations graph
- On disagreement, markdown wins — the index is always rebuilt by `reindex`

### Conceptual model
- **One cadence-memory directory = one context** (e.g. "platform A"). Contexts of different projects don't overlap — for another project, spin up a separate directory.
- A cadence-memory directory is itself a git repository.
- It contains a `config.yaml` describing paths to projects and to documents inside them.
- Documents are `.md` only. For project documents, annotations may be set in the config (curator's external view) or in frontmatter (author's internal view).
- The cadence-memory directory itself may also hold shared documents (workflows, glossary) — their annotations come only from frontmatter.
- The database is updated **only** by manual `cadence-memory reindex`. No watchers.
- The SQLite index lives directly in the directory (`index.sqlite`, in `.gitignore`).
- No web UI. CLI only.
- Ephemeral knowledge — short-lived documents (1-2 tickets for the current task), added/cleaned via the CLI.
- A skill for Claude Code that calls the cadence-memory CLI (rather than MCP — for read-only search MCP is overkill).

---

## 4. On-disk layout

```
my-platform-memory/                  # itself a git repo
  .git/
  config.yaml                        # project map: paths + exclude + optional discover rules
  annotations-config.yaml            # documents and their annotations (generated by discover, hand-edited)
  annotations-config.yaml.proposed   # intermediate discover output before --apply, in .gitignore
  index.sqlite                       # generated by reindex, in .gitignore
  workflows/                         # any shared .md
    deploy-process.md
    code-review-checklist.md
  glossary.md                        # also shared
  ephemeral/                         # short-lived knowledge, in .gitignore
    .gitkeep
  .gitignore
```

Default `.gitignore`:
```
index.sqlite
index.sqlite-journal
index.sqlite-wal
index.sqlite-shm
annotations-config.yaml.proposed
ephemeral/
*.tmp
```

If you later want to share a prebuilt index between machines, the `commit_index: true` flag in the config drops `index.sqlite` from ignore.

---

## 5. Two configs

There are two configs to separate **what to look at** (hand-edited only) from **what was found** (edited both by hand and by the `discover` skill).

### 5.1 config.yaml — project map

```yaml
# Project root paths and what NOT to index inside them
projects:
  - name: billing
    path: ~/code/billing
    exclude:
      - "node_modules/**"
      - ".venv/**"
      - "**/test_*.md"
    # Optional rules for discover: if a file matches, kind comes from here and
    # the skill doesn't guess. Files that don't match are decided by the skill.
    discover:
      kind_rules:
        - pattern: "README.md"
          kind: service
        - pattern: "docs/adr/*.md"
          kind: adr

  - name: orders
    path: ~/code/orders
    exclude: ["node_modules/**", ".venv/**"]

# Shared documents from the cadence-memory directory itself (workflows/, glossary.md, etc.)
globals:
  include: ["**/*.md"]
  exclude: ["ephemeral/**", "annotations-config.yaml*"]

# Defaults applied when annotations are missing
defaults:
  kind: doc

# Optional: whether to commit the SQLite index (see §4)
commit_index: false
```

The minimum needed to work is just `projects[].path`. `exclude` and `discover` are optional.

The project's `.gitignore` is **not** consulted — all exclusions are explicit globs only.

### 5.2 annotations-config.yaml — document list

Generated by `discover` (see §11.1) and hand-edited. Structure: a list of documents with annotations:

```yaml
documents:
  - id: billing:README.md
    project: billing
    path: README.md
    kind: service
    title: Billing Service
    tags: [payments, stripe, money]
    related: [orders:README.md]

  - id: billing:docs/architecture.md
    project: billing
    path: docs/architecture.md
    kind: pattern
    title: Billing data model

  # Document with no annotations — kind/title come from frontmatter or defaults
  - id: billing:docs/api.md
    project: billing
    path: docs/api.md

  - id: orders:README.md
    project: orders
    path: README.md
    kind: service

  # Global document (project: null)
  - id: :workflows/deploy-process.md
    path: workflows/deploy-process.md
    kind: pattern
    title: Deploy process
```

`reindex` reads both configs: from `config.yaml` it takes the roots and `globals`, from `annotations-config.yaml` the document list and their annotations. A document **not** in `annotations-config.yaml` is **not** indexed — even if it physically exists in the project. This is intentional: a single point of control, no surprises.

---

## 6. Annotation priority

A hard rule, to avoid mush:

| Field | Rule | Reasoning |
|---|---|---|
| `kind`, `title` | **frontmatter wins** | The author of the file is closer to the code than a discovery script. Frontmatter is a deliberate act; annotations-config is machine-generated with optional editing. |
| `tags`, `related` | **merge** (set union) | Both author and curator/skill can add links; both are right. |
| `project`, `confidence`, `last_confirmed_at` | when present, conflict = **frontmatter wins** | — |

Document with no annotations anywhere → `kind = defaults.kind`, `title` = first H1 heading or filename.

`cadence-memory status` should show provenance explicitly: "for billing/README.md kind came from frontmatter (`service`), overrode annotations-config (`doc`); tags merged: annotations-config → [stripe], frontmatter → [api, public]".

---

## 7. Markdown document schema

Frontmatter (everything optional if there's an annotation in config):

```markdown
---
kind: service                         # service | pattern | adr | glossary | task | doc
title: Billing Service
tags: [payments, stripe, money]
related: [orders:README.md, :workflows/deploy-process.md]
confidence: curated                   # curated | auto
last_confirmed_at: 2026-05-01
---

# Billing Service

## Stack
Python 3.12, FastAPI, PostgreSQL, Stripe SDK.

## Public API
- POST /charges — create a charge
- POST /refunds — refund
- GET /invoices/{id}

## Events emitted
- billing.charge.succeeded
- billing.charge.failed
- billing.refund.created

## Owners
team-payments
```

### Document IDs
Stable and human-readable: `<project>:<relative_path>` for project documents, `:<relative_path>` for global ones, `eph:<id>` for ephemerals.

```
billing:README.md
billing:docs/api.md
:workflows/deploy-process.md
eph:JIRA-1234
```

In `related`, use full IDs. (Aliases like `related: [orders]` → resolve to a project's main document — defer.)

---

## 8. Change detection

Three hashes are stored per document in the DB:
- `content_hash` — SHA256 of the `.md` body (frontmatter stripped)
- `frontmatter_hash` — SHA256 of the frontmatter block of the document
- `annotation_hash` — SHA256 of the final merged annotations (entry from `annotations-config.yaml` + frontmatter after applying §6 priority rules)

The source of the document list is **only `annotations-config.yaml`**. `config.yaml` provides the project root `path` (for resolving relative paths) and global `defaults`. `exclude` and `discover.kind_rules` from `config.yaml` only affect `discover`, not `reindex`.

On `reindex`:
1. Load both configs. Build the expected document list from `annotations-config.yaml`. Resolve absolute paths: project documents → `projects[name=...].path + path`; global documents → `<store_dir> + path`.
2. If the file at the resolved path is missing → hard error (a typo in `path` or a deleted file shows up immediately). Support `optional: true` on a document entry for cases where a file may legitimately be absent.
3. For each: read the file, compute the three hashes, compare to the DB.
4. If the document is not in the DB → INSERT.
5. If `content_hash` changed → UPDATE body + FTS index.
6. If `frontmatter_hash` changed → recompute `annotation_hash` (§6 priority rules may yield a different result) → UPDATE metadata, leave FTS alone.
7. If only `annotation_hash` changed (the entry in `annotations-config.yaml` was edited, frontmatter unchanged) → UPDATE metadata, leave FTS alone.
8. Documents that are in the DB but not in `annotations-config.yaml` → DELETE (with a warning in the log).

`annotations-config.yaml.proposed` is **ignored** by `reindex` — it's a discover intermediate; only the final `annotations-config.yaml` is indexed.

`cadence-memory status` is the same as reindex but a dry run: it shows "N will change, M will be added, K will be deleted" without writing anything. It also shows the source of any conflicting fields (see §6). Useful before committing either of the two configs.

---

## 9. SQLite schema

```sql
CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,           -- 'project' | 'global' | 'ephemeral'
  project TEXT,                        -- NULL for global/ephemeral
  abs_path TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,                  -- raw markdown without frontmatter
  content_hash TEXT NOT NULL,
  frontmatter_hash TEXT NOT NULL,
  annotation_hash TEXT NOT NULL,
  mtime INTEGER NOT NULL,
  indexed_at TEXT NOT NULL
);

CREATE TABLE tags (
  doc_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (doc_id, tag),
  FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE relations (
  src_id TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  PRIMARY KEY (src_id, dst_id),
  FOREIGN KEY (src_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE documents_fts USING fts5(
  id UNINDEXED,
  title,
  body,
  tags,
  tokenize='porter unicode61'
);

CREATE INDEX idx_documents_kind ON documents(kind);
CREATE INDEX idx_documents_project ON documents(project);
CREATE INDEX idx_documents_source ON documents(source_type);
```

Notes:
- Yes, store the body in the DB. Size is negligible, `get` doesn't hit disk, and ephemeral documents (which can live anywhere) are handled uniformly.
- No embeddings in MVP. FTS5 is enough while there are <1000 documents.
- `relations` has no link kind in MVP. If `depends_on` vs `emits_event_to` is later needed — add a `kind` column.

---

## 10. CLI

```
cadence-memory init                                 # config.yaml, annotations-config.yaml (empty), .gitignore, git init
cadence-memory reindex [--verbose]                  # rebuild the index from both configs + documents
cadence-memory status                               # dry-run: what changed since last reindex

cadence-memory discover [--project NAME] [--apply]  # see §11.1: launches Claude with a discover skill on .md files
                                                    # without --project — all projects from config.yaml
                                                    # without --apply — writes to annotations-config.yaml.proposed
                                                    # with --apply — overwrites annotations-config.yaml

cadence-memory list [--kind K] [--project P] [--format json|table]
cadence-memory query <text> [--kind K] [--project P] [--limit N] [--format json|table]
cadence-memory get <id>                             # raw markdown
cadence-memory show <id>                            # metadata + body, formatted

cadence-memory ephemeral add <path> [--id <id>] [--kind K] [--title T] [--tags ...]
cadence-memory ephemeral list
cadence-memory ephemeral remove <id>
cadence-memory ephemeral clear

cadence-memory chat [-- args for claude]            # launch Claude in this context (see §11.2)
```

### Locating the context: env var, not --store
To avoid passing `--store /path` to every command, the CLI determines the "current memory directory" in this order:
1. `--store <path>` flag if provided
2. `CADENCE_MEMORY_DIR` env var
3. Walk up from cwd to the first `config.yaml` next to `index.sqlite` (the way git looks for `.git`)
4. Otherwise an error with a hint

This is critical for the skill scenario: `cadence-memory chat` sets the env var and launches Claude — every subsequent invocation from the skill is automatically in the right context.

### Ephemeral knowledge
`ephemeral add` copies a file into `ephemeral/` (or symlinks it — flag of choice) and writes a row into the DB with `source_type='ephemeral'`. Ephemeral docs participate in `query`/`list` alongside the rest, but are easy to filter out. `clear` removes everything with `source_type='ephemeral'` — both from the DB and physically from `ephemeral/`.

Optional: `cadence-memory ephemeral add --inline -` reading stdin, to jot down a note quickly without a file.

---

## 11. Skills for Claude Code

Two skills: one for **discovery** (write mode, populates `annotations-config.yaml`), one for **queries** (read-only, used in `chat`). Both call the cadence-memory CLI through bash.

Why a skill rather than MCP:
- A skill via bash already knows how to call any CLI and parse the output. It's the native Claude Code path.
- MCP is appropriate when there's a stateful server, structured streaming, real-time events. Here we have read-only search + bulk writes from discovery. CLI is enough.
- A CLI is callable by hand for debugging, exposes exit codes, and is testable like a regular program.
- No MCP runtime, no risk of breaking changes in the protocol.
- Skills live as `.md` files in `~/.claude/skills/<name>/SKILL.md` — reusable across all memory directories.

### 11.1 Discover skill — annotation bootstrap

Invoked via `cadence-memory discover [--project NAME] [--apply]`. Under the hood, the CLI:
1. Reads `config.yaml`, builds the list of projects (one or all).
2. For each project, gathers `.md` files: `find <path> -name '*.md'` minus the `exclude` globs. The project's `.gitignore` is NOT consulted.
3. Launches Claude as a subprocess (the way cadence does) with the embedded prompt from `defaults/prompts/discover.txt` and the skill described below. Context passed in: project name, path, the discovered `.md` files, optional `kind_rules` from config, and the current `annotations-config.yaml` (for context only — it's overwritten anyway).
4. Through the skill, Claude reads the files, generates annotations, and writes the result to `annotations-config.yaml.proposed` (or directly to `annotations-config.yaml` with `--apply`).

**Idempotency:** rerunning **overwrites** the result — no incremental merge. If a human edited `annotations-config.yaml` by hand and wants to protect their edits, they commit the file and diff after the next discover.

**Skill:**

```markdown
---
name: cadence-memory-discover
description: Use ONLY when invoked by `cadence-memory discover` to bootstrap
  annotations for a multi-service codebase. Reads .md files from given project
  paths and writes a structured annotations-config.yaml entry list.
---

# Cadence Memory Discover

You are bootstrapping a knowledge index. The runner gives you:
- one or more projects with `name` and absolute `path`
- a list of `.md` files to annotate (already filtered by exclude rules)
- optional `kind_rules`: glob patterns that pin `kind` for matching files
- the current `annotations-config.yaml` content (for reference only — your
  output replaces the section for these projects entirely)

## What you produce
A YAML fragment with a `documents:` list. One entry per file:
```yaml
- id: <project>:<relative_path>      # `:<relative_path>` for globals
  project: <project_name>            # omit for globals
  path: <relative_path>
  kind: service|pattern|adr|glossary|task|doc
  title: <short human title>
  tags: [<lowercase, kebab-case>]
  related: []                        # only if you are confident
```

## How to choose `kind`
1. If file matches a `kind_rules` pattern — use that kind, no guessing.
2. Otherwise infer from path + content:
   - `README.md` at project root → `service`
   - `docs/adr/*.md`, files starting with "ADR-" → `adr`
   - `docs/architecture*.md`, `docs/patterns/*.md` → `pattern`
   - `glossary.md`, `terms.md` → `glossary`
   - everything else → `doc`

## How to choose `title`
First H1 of the file. If none — humanize the filename.

## How to choose `tags`
Read the first ~100 lines. Pick 2-5 lowercase kebab-case tags reflecting
the domain (`payments`, `webhook`, `migration`). Do NOT invent tags from
file paths or generic words like `documentation`, `readme`.

## What NOT to do
- Do not include files outside the provided list.
- Do not write `confidence` or `last_confirmed_at`.
- Do not invent `related` links — leave the list empty unless the file
  explicitly references another known document by path or service name.
- Do not modify the file's content. You only annotate.

## Output
Write the final YAML to the path the runner gave you (proposed or final).
Do not print extra prose.
```

### 11.2 Query skill — for chat

Used in `cadence-memory chat`. Read-only.

```markdown
---
name: cadence-memory
description: Use when working with a multi-service codebase represented by a
  cadence-memory knowledge base (set via CADENCE_MEMORY_DIR or auto-detected).
  Provides cross-project architecture, patterns, and ephemeral task context
  via the cadence-memory CLI.
---

# Cadence Memory

The user has a knowledge base of multiple services. Use the CLI to explore it
before making architectural suggestions or task plans.

## Discover what's there
Always start with: `cadence-memory list --format json`
This returns all documents (projects, kinds, titles).

## Search by topic
`cadence-memory query "<keywords>" --format json [--kind service|pattern|...] [--project <name>] [--limit 10]`

## Read a specific document
`cadence-memory get <id>`  (raw markdown)

## Check ephemeral (short-term task context)
`cadence-memory ephemeral list --format json`
Always check this first — it usually contains what the user is currently
working on.

## Workflow for planning tasks
1. `ephemeral list` to see current task context.
2. `query` for relevant services, patterns, ADRs.
3. `get` full content of top hits.
4. Synthesize a plan that respects the existing patterns.
```

### Typical bootstrap scenario

```bash
cadence-memory init
# fill out projects[] in config.yaml by hand
cadence-memory discover                       # → annotations-config.yaml.proposed
diff annotations-config.yaml annotations-config.yaml.proposed
# fix things by hand, or rerun with --apply
cadence-memory discover --apply               # overwrites annotations-config.yaml
cadence-memory reindex
```

For a single project:
```bash
cadence-memory discover --project billing --apply
cadence-memory reindex
```

### Typical ephemeral scenario
Task: "fix the billing-notifications interaction", needs to be discussed with Claude in the context of recent changes in two tickets:

```bash
cd ~/cadence-memory/platform/
cadence-memory ephemeral add ~/code/billing/docs/JIRA-1234-notes.md \
  --kind task --title "JIRA-1234 refund webhook"
cadence-memory ephemeral add ~/tmp/JIRA-1245-investigation.md \
  --kind task --title "JIRA-1245 notification race"
cadence-memory chat
# discuss; the skill pulls in both permanent and ephemeral knowledge
# ...
cadence-memory ephemeral clear
```

---

## 12. Cadence integration (future work)

Not part of cadence-memory MVP, but the whole point of the exercise. Three hooks:

1. **Pre-plan injection (RAG-style).** Add a `{{MEMORY_CONTEXT}}` variable to `make_plan.txt` that's filled from the cadence-memory CLI under a strict token budget.
2. **Skill during Q&A.** So Claude can request details through the CLI itself.
3. **Post-run auditor.** After `--task`/`--review`, parse the stream-json log and generate task documents. Without this the base goes stale. This is the AXME-style auditor — a separate task.

In `.cadence/config.yaml` (cadence side), add something like:
```yaml
memory:
  enabled: true
  store_dir: ~/cadence-memory/platform
  plan_context_token_budget: 2500
```

---

## 13. Out of scope for MVP

- Embeddings / semantic search — FTS5 is enough while there are <1000 documents
- Aliases for `related` (resolve `orders` → `orders:README.md`) — full IDs are required
- A change watcher — explicitly rejected
- Commands that mutate permanent knowledge (`note new` etc.) — the user edits `.md` by hand, then `reindex`
- In-SQLite versioning — git around the directory already provides it
- Multiple stores / namespaces — one store = one folder
- Web UI
- Conflict resolution for concurrent edits — last-write-wins + git as a safety net
- Auto-extraction (post-run auditor) — separate task after MVP
- Inferring stack/API from `pyproject.toml`, `package.json`, OpenAPI, etc. — discover only works with `.md`. If a project has no documentation, there is none, and discover does not invent it.
- Incremental discover (merging into the existing `annotations-config.yaml`) — we overwrite wholesale, edits are protected via git diff.
- Honoring the project's `.gitignore` in discover/exclude — only explicit globs in `config.yaml`.

---

## 14. Open questions before coding

(For behavior when a file from `annotations-config.yaml` is missing, see §8: hard error + `optional: true`.)

1. **Can the same physical path appear twice (in different projects)?**
   Decision: forbid it — too confusing. IDs must be unique; project membership is semantic, not physical.

2. **What about a `.cadence-memory/` subfolder inside a regular repo?**
   Decision: don't do it. The memory directory is always its own repo, not to be confused with the `.cadence/` config of cadence itself. Two different layers.

3. **`commit_index: true` — flag for sharing a prebuilt index?**
   Decision: add the flag, default `false` (index is gitignored). Enable when you want to share the index between machines.

4. **Symlink vs copy for `ephemeral add`?**
   Decision: a `--symlink` flag, default is copy. Copy is safer (the source file may be deleted/edited); symlink is handier for big files you keep editing.

---

## 15. Implementation plan

Steps; each is a self-contained useful artifact:

1. **Store layer** — schema, reading/writing markdown with frontmatter (pyyaml + python-frontmatter), SQLite index, reindex with three hashes. Round-trip tests and change-detection tests.
2. **Config loader** — parsing `config.yaml` and `annotations-config.yaml`, validation, applying the annotation priority rules (frontmatter wins), `~` and relative path expansion.
3. **CLI baseline** — Typer (as in cadence): `init`, `reindex`, `status`, `list`, `query`, `get`, `show`. JSON and table output formats. Auto-detect store via env var and walk-up.
4. **Ephemeral commands** — `add`/`list`/`remove`/`clear`. Ephemerals participate in `query`/`list` alongside the rest.
5. **Query skill** — `defaults/skills/cadence-memory.md`, ready to drop into `~/.claude/skills/cadence-memory/SKILL.md`.
6. **`cadence-memory chat`** — a thin wrapper that sets the env var and launches Claude with the query skill.
7. **Discover** — the `discover [--project] [--apply]` command, embedded prompt (`defaults/prompts/discover.txt`) and discover skill (`defaults/skills/cadence-memory-discover.md`). Scan `.md` against `exclude` globs, launch Claude as a subprocess (reuse the `ClaudeExecutor` approach from cadence), write to `.proposed` or to the target. Tests with a mocked ClaudeExecutor.

Steps 1-2 are a day or two of work. By step 4 there's already a working read-only product. Step 7 is a separate iteration on top of the finished store.

After MVP (separate tasks):
- Cadence integration (pre-plan injection)
- Post-run auditor
- Repo scanner
- Optional embeddings

---

## 16. Stack, dependencies, and style

### 16.1 Stack

- **Python 3.14+** (`requires-python = ">=3.14"`, `target-version = "py314"`)
- **Typer ≥0.9** — CLI (as in cadence)
- **PyYAML ≥6.0** — both yaml configs
- **python-frontmatter ≥1.1** — markdown-with-YAML-frontmatter parser (a thin wrapper over PyYAML, saves the manual regex for the `---` separator)
- **sqlite3** — stdlib; FTS5 is on by default in the Python builds shipped on macOS/Linux
- **PDM** — dependency and build manager (`pdm-backend`)

The Claude launch in `discover` is streaming, via `claude --output-format stream-json --print --verbose --dangerously-skip-permissions`, with line-by-line JSON event parsing, an idle watchdog, and process-group cleanup on Ctrl+C. This is the **same approach** as in cadence (`src/cadence/executor/`); the modules `executor/events.py` and `executor/process_group.py` are copied from cadence verbatim, and `claude_executor.py` is trimmed (no CADENCE signal detection and no error/limit pattern matching, neither of which is needed here). No new runtimes and no `anthropic` SDK are pulled in.

### 16.2 Dev dependencies

- `pytest ≥8.0`, `pytest-mock ≥3.14`, `pytest-cov ≥5.0`
- `ruff ≥0.4` (lint + format, config `select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]`, `line-length = 100`)
- `mypy ≥1.10` with `strict = true`
- `types-PyYAML`

`make check` = `ruff check + mypy + pytest`. Tooling runs straight out of `venv/bin/activate`, not via `pdm run` (see cadence CLAUDE.md).

### 16.3 Style

- `mypy --strict`, no `Any`, all Protocol boundaries annotated
- **Protocol-based interfaces** for every external dependency (`Store`, `ConfigLoader`, `FrontmatterParser`, `ClaudeRunner`) — tests mock the Protocol, not real SQLite/files/subprocess
- Embedded defaults (`config.yaml`, `annotations-config.yaml`, prompts, skills) live under `src/cadence_memory/defaults/`, read via `importlib.resources`
- Dataclasses (`@dataclass(frozen=True, slots=True)` where reasonable) for configs and DTOs
- No global mutable state; everything is passed as parameters
- Commit message format: see cadence CLAUDE.md (`<branch>. Added/Changed/Deleted: ...`); author = user; no `Co-Authored-By`

### Proposed layout

```
src/cadence_memory/
  cli.py                          # Typer entrypoint
  config.py                       # Config + AnnotationsConfig dataclasses, YAML loading, validation
  store/
    schema.py                     # SQL DDL
    sqlite_store.py               # Store Protocol implementation
    interface.py                  # Store Protocol
  documents/
    parser.py                     # frontmatter parsing
    annotations.py                # merge annotations-config + frontmatter (frontmatter wins)
    hashes.py                     # the three hashes
    ids.py                        # ID generation and parsing
  reindex/
    engine.py                     # incremental reindex
    diff.py                       # status / dry-run
  discover/
    scanner.py                    # .md walker with exclude globs
    runner.py                     # Claude subprocess launch with the discover skill
  ephemeral.py                    # ephemeral commands
  formatters/                     # json | table output
  defaults/
    config.yaml                   # config template for init
    annotations-config.yaml       # empty annotations-config template for init
    prompts/
      discover.txt                # embedded discover prompt
    skills/
      cadence-memory.md           # query skill
      cadence-memory-discover.md  # discover skill
```

---

## Source: cadence

Main repository: https://github.com/Drozdetskiy/cadence

Key files for understanding the style:
- `CLAUDE.md` — instructions for Claude Code in the repo
- `src/cadence/cli.py` — Typer entrypoint, phase dispatch
- `src/cadence/config.py` — Config dataclass + YAML pattern
- `src/cadence/processor/` — runner, phase orchestration
- `src/cadence/defaults/prompts/` — embedded prompts
- `Makefile` — `make check` = `lint + typecheck + test`
