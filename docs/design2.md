# cadence-memory v2 — design document

This document supersedes `docs/design.md`. The previous design is being deleted in full — there is no backwards compatibility, no migration path, and the CLI surface, on-disk layout, and storage model all change.

The new approach follows Andrej Karpathy's [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) idea, extended in spirit by the QMD-based wiki workflow described in `docs/article-about-db.md`. The reframing is:

- **Old design**: a per-project SQLite/FTS5 index that retrieves raw chunks at query time (RAG). Most of the recent work — chunking, identifier boost, query expansion, Claude rerank, enrichment — was patching that retrieval pipeline.
- **New design**: a single master wiki — a structured, interlinked tree of markdown pages — that an LLM compiles **once on ingest** and **keeps current** as new commits land. The LLM does the bookkeeping; the human curates sources and asks questions.

The wiki, not an SQLite index, is the artifact. Search exists (`qmd` MCP) but is a navigation aid, not the centerpiece.

---

## 1. Context and goal

### 1.1 What changes vs v1

| Axis | v1 (old) | v2 (new) |
|---|---|---|
| Source of truth | `.md` files inside each project repo | A single dedicated **master wiki repo** owned by the user |
| Index | SQLite + FTS5 (chunks, mentions, enrichment, boosts) | The wiki itself (`index.md`, `[[wikilinks]]`); search via `qmd` MCP |
| Ingest unit | A markdown file the user authored | A **git commit** in a tracked source repo |
| Update model | Manual `reindex` after the user edits docs | Background **worker** that walks new commits and asks Claude to update the wiki |
| Per-project layout | Per-repo wiki/notes | One master wiki repo, with a subtree per source repo |
| Search-side stack | Identifier boost, query expansion, Claude rerank | None of these. `qmd` does hybrid BM25 + vector + LLM rerank locally |
| LLM role | Annotates / enriches metadata for retrieval | **Authors** the wiki end to end |

### 1.2 What v2 keeps from v1

- **Streaming Claude executor** — subprocess + `--output-format stream-json` + idle watchdog + signal-safe process-group cleanup. Used for every LLM call.
- **Frontmatter parser + page conventions** — every wiki page carries YAML frontmatter (`title`, `type`, `source`, `created`, `updated`, `tags`, `confidence`). This is non-negotiable per Karpathy: without it Claude generates inconsistent pages.
- **Multi-stage bootstrap** — the 5-stage prompt sequence from the article (data model → routes/controllers → architecture → gaps → plans/todos), adapted to be stack-agnostic, used as an alternative to commit-walking for legacy repos.
- **Protocol-based interfaces, mypy --strict, no `rich`, dataclasses with `frozen=True, slots=True`, `importlib.resources` for embedded defaults** — same engineering bar as v1.

Everything else from v1 is deleted.

### 1.3 Non-goals (explicit)

- Per-project wiki folders. There is **only the master wiki**.
- Web UI / browser viewer. The wiki is a git repo of markdown files; users browse it in Obsidian or any editor.
- Any retrieval tuning (chunking, identifier boost, query expansion, Claude rerank, mentions/backlinks indexing). `qmd` covers search; wikilinks cover navigation.
- Real-time push from source repos. The worker pulls; source repos do not need to know about cadence-memory.
- Backwards compatibility with v1 stores or configs.

---

## 2. Core principles

1. **Knowledge compiles on ingest, not on query.** When a commit lands, the LLM reads the diff and updates relevant wiki pages, the index, and the log. At query time the wiki is *already* synthesized.

2. **The LLM owns the wiki layer.** A human rarely edits wiki pages directly. The user maintains `config.yaml` (which repos, which branches), drops sources into `raw/notes/`, and asks questions. Claude does the writing, cross-referencing, contradiction-noting, and bookkeeping.

3. **One commit ≈ one ingest event.** The unit of work is a commit, not a file or a project. This mirrors how knowledge actually accumulated — schema migrations, ADRs, refactors all show up at specific points in history. The wiki tracks that evolution.

4. **The master wiki is a git repo.** It has its own history, branches, and PRs. The worker commits its own changes. A human can review, revert, or hand-edit any page; the LLM respects manual edits on the next pass.

5. **Search is a navigation tool, not an answer engine.** `qmd` MCP gives Claude a way to find pages quickly during a session. The synthesis itself is on the page, not derived from raw retrieval.

6. **Headless, budgeted, sandboxed Claude.** Every worker LLM call uses `claude -p --max-budget-usd <cap> --allowedTools <readwrite-only> --output-format stream-json`. No surprise costs, no network egress beyond Anthropic, no plugin drift.

---

## 3. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ User                                                                │
│   - edits config.yaml in master-wiki repo                           │
│   - drops articles into master-wiki/raw/notes/                      │
│   - asks Claude Code questions; reviews PRs                         │
└─────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ master-wiki repo  (one git repo, owned by user)                     │
│   config.yaml                — repos, model, budgets, schedule      │
│   index.md                    — full catalog                        │
│   log.md                      — chronological audit                 │
│   gaps.md                     — open questions                      │
│   decisions.md                — ADR-style                           │
│   patterns.md, learnings.md   — cross-project synthesis             │
│   projects/<repo>/            — per-source-repo subtree             │
│     overview.md, data-model.md, architecture.md, ...                │
│   raw/notes/                  — manual drop-zone                    │
│   .cadence-memory/state.json  — worker state (last SHA per repo)    │
│   .claude/settings.json       — SessionStart hook + skills          │
│   CLAUDE.md                   — wiki schema & query protocol        │
└─────────────────────────────────────────────────────────────────────┘
                ▲
                │ writes
                │
┌─────────────────────────────────────────────────────────────────────┐
│ cadence-memory  (Python CLI + worker)                               │
│                                                                     │
│   commands:                                                         │
│     init                — scaffold a master-wiki repo               │
│     repos add/list/rm   — edit config.yaml                          │
│     bootstrap <repo>    — 5-stage initial pass for a fresh repo     │
│     worker run          — process all pending commits, exit         │
│     worker daemon       — long-running poller                       │
│     lint                — wiki health check                         │
│     query <text>        — shells out to qmd (or rg fallback)        │
│                                                                     │
│   internals:                                                        │
│     git_cache/           — clones of source repos under XDG_CACHE   │
│     streaming executor   — Claude subprocess wrapper (kept from v1) │
│     git walker           — iterates new commits, batches noise      │
│     prompts/             — ingest, bootstrap-N, lint templates      │
└─────────────────────────────────────────────────────────────────────┘
                │
                │ reads (clone + fetch)
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ source repos (read-only to cadence-memory)                          │
│   project-a, project-b, ...                                         │
└─────────────────────────────────────────────────────────────────────┘
                ▲
                │ MCP search
                │
┌─────────────────────────────────────────────────────────────────────┐
│ qmd  (Tobi Lütke's local search; MCP server)                        │
│   indexes the master-wiki tree; Claude Code calls it during chats   │
└─────────────────────────────────────────────────────────────────────┘
```

The master wiki repo is the working directory of `cadence-memory`. The CLI is invoked from inside it (or pointed at it via `--wiki <path>` / `CADENCE_MEMORY_WIKI` env). The wiki repo's `.gitignore` excludes `git_cache/` and `.cadence-memory/state.json` files that should not be committed.

---

## 4. Master wiki repo layout

```
my-master-wiki/                       # itself a git repo
  .git/
  config.yaml                         # the only hand-edited config
  CLAUDE.md                           # wiki schema + query protocol (Claude reads this every session)
  .claude/
    settings.json                     # SessionStart hook, allowed tools
    skills/
      wiki-researcher/
        SKILL.md                      # used by /plan and read-side queries
      wiki-ingest/
        SKILL.md                      # used to integrate raw/notes/ manually
  index.md                            # full catalog (LLM-maintained)
  log.md                              # append-only chronological audit
  gaps.md                             # open questions and TODOs
  decisions.md                        # cross-project ADRs
  patterns.md                         # patterns seen in 2+ projects
  learnings.md                        # gotchas & hard-won lessons
  glossary.md                         # shared terms (optional, hand-editable)
  projects/
    <repo-slug>/
      overview.md                     # one-page project summary
      data-model.md                   # ER + per-entity pages
      architecture.md                 # services, jobs, integrations
      routes.md                       # HTTP/RPC surface (when applicable)
      decisions.md                    # repo-local ADRs (cross-project ones go up)
      gaps.md                         # repo-local open questions
      models/<entity>.md              # one page per significant entity
      services/<service>.md           # one page per significant service
  raw/
    notes/                            # manual drop-zone for articles, PDFs (md), meeting notes
    assets/                           # images for raw notes (optional)
  .cadence-memory/
    state.json                        # last-processed SHA per repo (auto-generated, not in git)
    git_cache/                        # local clones of source repos (not in git)
  .gitignore
```

**Default `.gitignore`:**
```
.cadence-memory/state.json
.cadence-memory/git_cache/
*.tmp
```

**Page conventions** (Karpathy + article):

```yaml
---
title: "User"
type: model              # one of: model | service | controller | architecture | decision | pattern | overview | log | gaps
source: app/models/user.rb
project: project-a       # or "_master" for cross-project pages
created: 2026-05-08
updated: 2026-05-08
tags: [auth, billing]
confidence: high         # high | medium | low
---

**TLDR**: One sentence summary that previews what the page contains.

(rest of the page here, with [[wikilinks]] to other pages)
```

The frontmatter is mechanically parsed; the wiki schema in `CLAUDE.md` includes a worked example so Claude generates pages with this layout consistently. Wikilinks are simple `[[Page Name]]` or `[[projects/<slug>/models/User]]`. We do **not** maintain a separate mentions/backlinks index — Claude resolves them by reading `index.md` and grepping the tree.

---

## 5. Configuration (`config.yaml`)

The single hand-edited config. Lives at the root of the master wiki repo.

```yaml
# Default Claude model used for every LLM call. Overridable per-repo.
model: claude-sonnet-4-6

# Default per-call budget in USD. The worker passes --max-budget-usd to Claude.
# Per-call ≈ per-commit-ingest ≈ per-bootstrap-stage. Unbounded if absent.
budget_usd: 0.50

# Idle watchdog timeout for a single Claude call, in seconds.
idle_timeout_s: 300

worker:
  # How often `worker daemon` polls each repo for new commits.
  poll_interval_s: 3600
  # Commits whose subject matches any of these regexes are batched and fed to
  # Claude in a single call rather than one-by-one (deps bumps, formatter
  # passes, lockfile churn, generated code, etc.).
  noise_subject_patterns:
    - "^chore\\(deps\\):"
    - "^style:"
    - "^Bump "
    - "^Apply formatter"
  # Commits matching these are skipped entirely.
  skip_subject_patterns:
    - "^Merge branch "
  # Maximum number of commits to process in a single `worker run`. The next
  # run picks up from where it left off. Prevents a fresh repo from blowing
  # the API bill on first ingest.
  max_commits_per_run: 50

repos:
  - name: project-a                    # used as projects/<name>/ subtree slug
    url: git@github.com:org/project-a.git
    branch: main                       # base branch the worker tracks
    # Optional: don't walk the entire history — start here. Useful for very
    # old repos where pre-`start_commit` history isn't worth the spend.
    start_commit: abc1234
    # Optional per-repo overrides:
    model: claude-opus-4-7             # use a stronger model for this repo
    budget_usd: 1.00
    # Files/globs whose changes should NOT trigger ingest (LLM never sees them).
    exclude:
      - "**/*.lock"
      - "node_modules/**"
      - "vendor/**"

  - name: project-b
    url: https://github.com/org/project-b
    branch: develop

# Optional: paths under raw/notes/ to ingest automatically on next worker run.
# Default: manual via `cadence-memory ingest <path>` or the wiki-ingest skill.
raw_auto_ingest: false
```

Validation rules:
- `repos[].name` is unique, slug-shaped (`[a-z0-9-]+`), and is the directory name under `projects/`.
- `repos[].url` is reachable; the worker fails fast on first clone.
- Unknown top-level keys are an error (typo guard).

---

## 6. The worker

The worker is the engine of the new design. It catches a master wiki up to `HEAD` of each tracked source repo, then keeps it current.

### 6.1 Lifecycle

For each repo in `config.yaml`:

1. **Clone or fetch.** `git_cache/<repo>/` is a bare-ish working copy under `.cadence-memory/git_cache/`. First run does `git clone --filter=blob:none`; subsequent runs do `git fetch origin <branch>`.

2. **Determine the commit range.**
   - If `state.json[repo].last_sha` is unset → first run. Range is `<start_commit or root>..origin/<branch>`.
   - Else → range is `<last_sha>..origin/<branch>` (only new commits, in topological order).

3. **Filter and batch.** Walk the range with `git log --reverse --topo-order`:
   - Drop commits matching `worker.skip_subject_patterns`.
   - Group consecutive commits matching `worker.noise_subject_patterns` into a single ingest event ("apply N noise commits", subjects listed).
   - Cap at `worker.max_commits_per_run`. Remaining commits run on the next invocation.

4. **For each event** (single commit, or noise batch):
   - Gather context: commit subject + body, full `git diff` for that range, list of changed files.
   - Render the **ingest prompt** (§7), passing the wiki tree, current `index.md` head, and `log.md` tail as context.
   - Run `claude -p --max-budget-usd <budget> --allowedTools "Read,Write,Edit,Glob,Grep" --output-format stream-json`. The streaming executor parses events and applies the idle-timeout watchdog.
   - Claude writes/updates pages, `index.md`, `log.md` directly via `Write`/`Edit`.
   - On success, the worker stages the wiki diff (`git add -A` inside master wiki), commits with message `cadence-memory: ingest <repo> <short-sha> <subject>` (no `Co-Authored-By`), and updates `state.json[repo].last_sha`.
   - On Claude failure (non-zero exit, watchdog, budget exceeded): write a stub entry to `log.md` (`## [<date>] FAILED ingest <repo> <sha>: <reason>`), do NOT advance `last_sha`, continue with the next repo. The user can rerun.

5. **Periodic mode (`worker daemon`).** After processing all repos, sleep `worker.poll_interval_s`, repeat. SIGTERM exits cleanly between repos. SIGINT exits between commits.

### 6.2 State file

`.cadence-memory/state.json`:
```json
{
  "version": 1,
  "repos": {
    "project-a": {
      "last_sha": "abc1234...",
      "last_run_at": "2026-05-08T14:00:00Z",
      "commits_processed": 142,
      "last_failure": null
    }
  }
}
```

State is plain JSON, not SQLite — it's small and the user occasionally edits it (e.g. to roll back to a previous SHA after a bad ingest).

### 6.3 Concurrency & locks

A POSIX file lock on `.cadence-memory/worker.lock` prevents two `worker run` invocations from racing on the same wiki repo. Different wiki repos have different locks.

---

## 7. Ingest prompt and flow

The ingest prompt is the heart of the system. It tells Claude:

1. **What the wiki is.** Its schema (§4), its conventions, the frontmatter template, the wikilink syntax.
2. **What just happened.** The commit subject, body, diff (truncated to a safe size with notice), and changed files.
3. **What's already documented.** A short rendering of `index.md` (heads only, ~60 lines) + `log.md` tail (~15 entries).
4. **What to do.** Update affected pages. Create new pages for newly-introduced concepts. Update `index.md` with new entries. Append a `log.md` entry. If the diff contradicts an existing page, **flag the contradiction in `gaps.md`** rather than silently rewriting.
5. **What NOT to do.** Don't rewrite history. Don't touch `raw/`. Don't write outside `projects/<this-repo>/` or the cross-project root pages (`patterns.md`, `learnings.md`, `decisions.md`, `gaps.md`, `index.md`, `log.md`).

The prompt is embedded under `src/cadence_memory/defaults/prompts/ingest.txt` and templated with `string.Template`. The user can override it by dropping their own copy under `<wiki>/.cadence-memory/prompts/ingest.txt`.

**Diff size guard.** If the diff exceeds `~60k tokens`, the worker:
- Splits the commit by directory (top-level package or `app/<subsystem>/`).
- Runs separate ingest calls per shard, each sharing the same commit subject/body.
- Final `log.md` entry is one line per commit, not per shard.

**Newly-introduced concept = new page.** The prompt instructs Claude to err on the side of new pages, then to wikilink them in. Stale pages are picked up by `lint` (§9), not by the ingest pass.

---

## 8. Bootstrap (alternative for legacy repos)

For a repo whose history is too long, too noisy, or simply not informative (e.g. a giant squash-merge of years of history), commit-walking is wasteful. The `bootstrap` command runs the 5-stage prompt sequence from the article in one go:

1. **Data model.** Read schema files (`schema.rb`, `models.py`, `prisma/schema.prisma`, etc., per stack), generate `data-model.md` + `models/*.md`.
2. **Routes & controllers.** Read route definitions (`routes.rb`, FastAPI/`@app.route`, Express, etc.), generate `routes.md` + `controllers/*.md`.
3. **Architecture.** Read `services/`, `jobs/`, initializers, dep manifests; mine `git log --grep="refactor|migrate|breaking|architecture"` and recent merge commits; generate `architecture.md` + `services/*.md` + `decisions.md` (extracted ADRs).
4. **Gaps.** Read everything generated so far + raw schema + routes; generate `gaps.md` listing what's not yet documented and open questions; update `index.md` with the full catalog; append a bootstrap entry to `log.md`.
5. **Plans/todos.** If the repo has `plans/`, `todos/`, or `docs/decisions/`, ingest them as a final synthesis pass.

After bootstrap, `state.json[repo].last_sha` is set to the current `HEAD` of `branch`. The worker then starts incremental ingest from the next commit.

The user picks bootstrap **vs.** commit-walking via `--mode {commits,bootstrap}` on `worker run`, default `commits`. `cadence-memory bootstrap <repo>` is a convenience alias for `worker run --mode bootstrap --only <repo>`.

The 5 prompts each adapt to the detected stack: the bootstrap stage prompts open with a stack detection step (read manifests under the repo root) and switch their reading list accordingly. Stack detection is best-effort and falls back to "general — read README, top-level dirs, and the most-changed files in the last year."

---

## 9. Lint

`cadence-memory lint` runs Claude over the wiki itself (no source repos involved) and asks it to:

- Find orphan pages (no inbound wikilinks from `index.md` or any other page).
- Find broken wikilinks.
- Flag contradictions between pages where dates/claims diverge.
- Find concepts mentioned ≥3 times across pages but lacking their own page.
- Refresh `gaps.md` with the union of any open questions surfaced.

Output: a single PR-ready commit on a `lint/<date>` branch in the wiki repo (worker doesn't auto-merge), or an in-place commit on `main` if the user passes `--apply`.

`lint` is intended to be scheduled (`cron`, `launchd`, GitHub Actions in the wiki repo) — typical cadence: weekly per repo, monthly full.

---

## 10. Search (`qmd` MCP)

`qmd` ([Tobi Lütke's local search engine](https://github.com/tobi/qmd)) is the primary search tool. It does:
- BM25 keyword search
- Local vector embeddings
- Optional LLM rerank

**Integration:**
- The user installs `qmd` once (`brew install qmd` or per its README) and indexes the master wiki: `qmd index <wiki-path> --collection master`.
- A post-commit hook in the master wiki repo (`.git/hooks/post-commit`, installed by `cadence-memory init`) re-indexes after every commit (worker commits, manual edits, lint commits).
- `qmd`'s MCP server is registered with Claude Code globally; the wiki-researcher skill calls it as a native tool.

**Fallback.** If `qmd` is not installed, `cadence-memory query <text>` shells out to `rg --type md` over the wiki, returning matching files with one-line context. Claude Code skills detect `qmd` availability at session start and pick the search backend accordingly.

We do **not** ship our own search engine. SQLite/FTS5 (the v1 store) is gone. There is no "rerank-via-Claude" code in v2 — `qmd` does its own rerank, and the wiki itself is the synthesized layer.

---

## 11. CLI surface

```
cadence-memory init [<path>]
    Scaffold a master wiki repo at <path> (default: cwd). Writes config.yaml,
    CLAUDE.md, .claude/settings.json, .gitignore, raw/, projects/, and the seed
    index.md/log.md/gaps.md. Initializes git if not already a repo. Idempotent.

cadence-memory repos add <name> <url> [--branch BRANCH] [--start-commit SHA] [--model M] [--budget USD]
cadence-memory repos list [--format json|table]
cadence-memory repos remove <name>
    Round-trip-edit config.yaml preserving comments (ruamel.yaml).

cadence-memory bootstrap <repo>
    Run the 5-stage initial bootstrap for <repo>. Sets last_sha to current HEAD
    on success. Equivalent to `worker run --mode bootstrap --only <repo>`.

cadence-memory worker run [--mode {commits,bootstrap}] [--only REPO] [--limit N] [--dry-run]
    Process pending commits across all repos (or one if --only) and exit.
    --dry-run prints the planned ingest events without calling Claude.

cadence-memory worker daemon [--once]
    Long-running poller. SIGTERM-safe. --once is equivalent to `worker run`
    (kept for systemd compatibility).

cadence-memory ingest <path>
    Ingest a single file from raw/notes/ (or any path inside the wiki repo)
    into the wiki. Used by the wiki-ingest skill and for manual drops.

cadence-memory lint [--apply] [--only REPO]
    Run wiki health-check (§9). Default writes to a lint/<date> branch.

cadence-memory query <text> [--limit N] [--format json|table]
    Search the wiki via qmd if available, else ripgrep. Returns page hits.

cadence-memory status
    Print state: repos, last_sha, last_run_at, pending commits, recent failures.

cadence-memory --version
```

All commands accept `--wiki <path>` to target a specific master wiki, defaulting to `CADENCE_MEMORY_WIKI` env or walk-up from cwd.

`--format json` for every read command, machine-parseable. `--format table` for human view (manual columns + textwrap, no `rich`).

---

## 12. Skills (Claude Code)

Two skills, both invoke the CLI via shell — no MCP server of our own.

### 12.1 wiki-researcher

Used by `/plan` and any read-side question. Steps (encoded in the skill):
1. Read `<wiki>/index.md` head + `<wiki>/log.md` tail (via `cadence-memory status` summary).
2. Search via `qmd` MCP (or `cadence-memory query` fallback).
3. Read the top hits.
4. Synthesize a "Past Knowledge" section: relevant pages, applicable patterns, known gotchas, reusable components, gaps the current task could fill.

### 12.2 wiki-ingest

Used to integrate a manual drop. Steps:
1. User points the skill at a file under `raw/notes/`.
2. Skill calls `cadence-memory ingest <path>`, which renders the same ingest prompt (with the file as the source instead of a commit diff) and runs Claude.
3. Claude updates pages, index, log; returns a brief summary to chat.

Both skills are shipped under `src/cadence_memory/defaults/wiki/.claude/skills/` and copied into `<wiki>/.claude/skills/<name>/SKILL.md` by `init`.

---

## 13. Hooks

The wiki repo's `.claude/settings.json` includes a **SessionStart hook** that runs:

```bash
cadence-memory status --short && \
  head -60 index.md && \
  tail -15 log.md
```

This loads the wiki's catalog summary into Claude's context at the start of every session and after every `/clear`. Without it, Claude won't proactively consult the wiki — same finding as the article.

The wiki repo's `.git/hooks/post-commit` runs `qmd index . --collection master` (or the rg-based no-op if qmd isn't installed) so search stays current after every commit.

There is **no post-commit hook in source repos**. The worker pulls; we don't push from the source side.

---

## 14. Cost and budget

Per the article's measurements (Rails projects, mid-sized):
- Bootstrap: $0.50–1.00 per repo, one-time.
- Per-commit ingest: $0.05–0.15.
- Lint: $0.30–0.50 per repo, weekly.

Every Claude call passes `--max-budget-usd <cap>` derived from `config.yaml`. If the cap trips mid-call, the worker logs a `BUDGET_EXCEEDED` failure and does not advance `last_sha`. The user can raise the cap and rerun.

Per-call budgets prevent runaway cost; the worker has no global cap (composing per-call caps with `max_commits_per_run` is sufficient).

---

## 15. Storage and state

- **Master wiki = git.** Versioned, branchable, PR-able. The user can revert any wiki change.
- **Source repos = local clones under `.cadence-memory/git_cache/`**, not in the wiki's git tree. `--filter=blob:none` keeps them small.
- **Worker state = `.cadence-memory/state.json`**, also outside the wiki's git tree. Plain JSON. The user can edit it (e.g. to retry a commit).
- **No SQLite, no FTS5, no chunks table.** This is a clean break from v1.

---

## 16. Open questions / out of scope

- **Auth for private source repos.** First version assumes the worker runs as the user, with their `git` credentials available (SSH keys, Git Credential Manager). Token-based auth (PAT in env, deploy keys per repo) is a follow-up.
- **Rebases & force-pushes on the tracked branch.** If `origin/<branch>` no longer contains `last_sha`, the worker bails and asks the user to reset `state.json` (or run `bootstrap` again). Auto-recovery is out of scope for v1.
- **Multi-language / non-source repos.** A repo of mostly markdown (a docs repo, a blog) works through the same commit-walk path; the ingest prompt is general enough. No special handling.
- **Conflict resolution between manual wiki edits and ingest.** If a user hand-edits `projects/foo/data-model.md` and the next ingest disagrees, Claude is instructed (in the ingest prompt) to *preserve* manual edits and only append/extend, then flag the disagreement in `gaps.md`. Sharper conflict semantics are deferred.
- **Cross-project synthesis cadence.** `patterns.md` / `learnings.md` are updated incrementally on every ingest, but a periodic cross-project synthesis pass (mining all `projects/*/` for repeated patterns) is a v2.1 feature, not in v1.

---

## 17. Migration from v1

There is no migration. The user is deleting the v1 codebase in a single commit; v2 starts from an empty `src/`. The new package keeps the name `cadence-memory` (PyPI, Homebrew formula) but the major version bumps to `1.0.0` to mark the rewrite.

The v1 features list mapped to v2:

| v1 feature | v2 fate |
|---|---|
| SQLite `Store`, FTS5 schema, `documents`/`chunks` tables | **Deleted.** Replaced by the wiki + `qmd`. |
| Chunking (`chunker.py`, endpoint-aware OpenAPI chunker) | **Deleted.** A wiki page is the natural unit. |
| Identifier boost, query expansion, Claude rerank | **Deleted.** `qmd` provides hybrid search + rerank. |
| Enrichment (Claude-generated keywords/questions) | **Deleted.** The wiki page itself is the synthesis. |
| Mentions/backlinks indexing | **Deleted.** Wikilinks + `index.md` cover it. |
| `discover` (annotation extraction) | **Replaced** by the per-commit ingest flow. |
| `ephemeral` (raw/notes equivalent) | **Replaced** by `raw/notes/` + `wiki-ingest` skill. |
| Streaming Claude executor | **Kept**, ported. |
| Frontmatter parser | **Kept**, ported. |
| Multi-stage discover orchestrator | **Repurposed** as the 5-stage `bootstrap` command. |
| Projects management (`projects add/list/...`) | **Replaced** by `repos add/list/...`. |
| Skills (`cadence-memory`, `cadence-memory-discover`) | **Replaced** by `wiki-researcher`, `wiki-ingest`. |

The cdc-tasks/ workflow remains: each v2 feature gets a numbered `<NNNN>-<slug>/init` file under `cdc-tasks/`, executed in order via cadence. See `docs/features.md` for the v2 task list.
