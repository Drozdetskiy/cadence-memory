# cadence-memory v2 — features & implementation order

Companion to `docs/design2.md`. This file lists v2 features in the order they should be implemented as cadence tasks, and gives the **base prompt template** for the `cdc-tasks/<NNNN>-<slug>/init` files.

The numbering restarts from `1001` on a clean `src/` (the user wipes the v1 codebase in a single commit; v2 starts empty). Each task is sized to fit one cadence run (`cadence --plan` → `--task`, or `--run --impl --squash`) and ends with `make check` passing.

---

## 1. Cadence init prompt — base template

Use this template verbatim as the skeleton for every `cdc-tasks/<NNNN>-<slug>/init` file. Fill in the bracketed placeholders. Keep code samples concrete (paste real type signatures, real CLI examples) — vague inits produce vague code.

```markdown
# <Title>

## Goal

<2–6 sentences. WHY this task exists, WHAT user-visible behavior it adds or
changes, and HOW it fits into the design2.md picture (cite the section, e.g.
"design2 §6.1"). If this introduces a hard break, say so explicitly.>

## Files

- Create: `src/cadence_memory/<module>.py` — <one line: what it owns>
- Create: `tests/<area>/test_<module>.py` — <one line>
- Modify: `src/cadence_memory/cli.py` — <which command(s); what changes>
- Modify: `src/cadence_memory/defaults/<file>` — <only if defaults change>
- Modify: `CHANGELOG.md` — section for the new version (only on
  user-visible behavior changes; internal-only tasks skip this).
- Modify: `src/cadence_memory/__init__.py` — bump `__version__` (only on
  user-visible behavior changes; internal-only tasks skip this).

## Steps

### <Subsystem 1>

```python
# Concrete code. Real type signatures, real dataclasses, real Protocol
# definitions. Don't write "implement Foo" — write the Foo skeleton.
@dataclass(frozen=True, slots=True)
class Foo:
    bar: str
    baz: int

class FooStore(Protocol):
    def upsert(self, foo: Foo) -> None: ...
    def get(self, key: str) -> Foo | None: ...
```

Edge cases this subsystem must handle:
- <bullet>
- <bullet>

### <Subsystem 2>

<As above. One subsection per logically-distinct piece of work.>

### CLI wiring (if applicable)

```
cadence-memory <verb> <args> [--flag1] [--flag2 VALUE]
```

Show a real invocation, the expected exit codes, and what stdout/stderr look
like for the happy path and for the most likely failure.

## Tests

Write tests that exercise the actual behavior, not just type-correctness.

- `test_<...>` — <one line: what it asserts>
- `test_<...>` — <one line>
- For Protocol-typed dependencies: mock the Protocol, not the concrete impl.
- For Claude calls: mock the `ClaudeRunner` Protocol; **never** invoke a real
  `claude` subprocess in tests.
- For git operations: use `tmp_path` + `subprocess.run("git init", ...)`; do
  not hit network.
- For CLI: Typer `CliRunner`.

## Acceptance

- `pytest tests/ -v` passes (whole suite).
- `make check` passes (ruff + mypy --strict + pytest).
- `cadence-memory --version` runs (and prints the bumped version, if this
  task bumped it).
- <Any task-specific behavior assertion. E.g. "running `cadence-memory
  worker run --dry-run` against a fixture wiki prints exactly N planned
  ingests and writes nothing.">

## Out of scope

- <Things adjacent reviewers might expect to land here but that belong to a
  later task. Cite the task number, e.g. "Lint command — task 1017.">
- <Always include this section, even if short. It prevents scope creep.>

## Depends on

- Task <NNNN> for <what>. (Or "None" for the first few tasks.)
```

**Conventions inherited from the v1 task style** (keep these):
- Markdown subsections under `## Steps` use `###` and are named by subsystem.
- Code blocks are real Python / SQL / YAML / shell — not pseudo-code.
- Edge-case bullets directly under each subsystem's code block.
- Tests get their own top-level section, not nested under Steps.
- Acceptance bullets are testable assertions, not vibes.
- Out-of-scope is mandatory.

**Conventions for v2 specifically**:
- No SQLite work. If a task feels like it wants a table, it probably belongs in the wiki layer, not the Python layer.
- Every Claude call goes through `ClaudeRunner` Protocol; tests inject a fake. Real subprocess code lives only in `executor/claude_executor.py`.
- Every prompt template lives under `src/cadence_memory/defaults/prompts/` and is loaded via `importlib.resources`. No inline f-string prompts in business logic.
- Frontmatter validation runs on every page that the LLM produces and on every page the wiki repo already contains. Bad frontmatter is a hard error, not a warning.

---

## 2. Implementation order

Each item below is one cadence task. Tasks are listed in dependency order — no item builds on a later one. The "What" column is the elevator pitch for the task; the "Why first" column explains the ordering.

| # | Slug | What | Why this position |
|---|---|---|---|
| 1001 | `package-skeleton` | Empty package, Typer entrypoint, `make check`, `pdm` build, GitHub CI workflow. `cadence-memory --version` returns `1.0.0`. | Foundation. Everything else depends on the package being installable and CI being green. |
| 1002 | `config-schema` | `config.yaml` parser (PyYAML for read, ruamel.yaml for round-trip writes). Frozen dataclasses for `Config`, `RepoConfig`, `WorkerConfig`. Validation rules from design2 §5. | Every other module reads config; pin its shape early. |
| 1003 | `wiki-locator` | `resolve_wiki_dir()`: `--wiki` flag → `CADENCE_MEMORY_WIKI` env → walk-up looking for `config.yaml`. Used by every CLI command. | Cheap, but blocks every CLI subcommand. |
| 1004 | `frontmatter` | `python-frontmatter` wrapper → `ParsedPage(frontmatter, body, h1)`. Schema for the YAML block (design2 §4). Hard validation: missing required fields → exception with the offending file path. | Wiki pages are mechanically read in many places (lint, ingest context, status). |
| 1005 | `init-command` | `cadence-memory init [<path>]`: scaffold the master wiki repo (config.yaml, CLAUDE.md, .claude/, raw/, projects/, .gitignore, seed index.md/log.md/gaps.md, `git init`). Idempotent. Embedded defaults via `importlib.resources`. | Needed before any worker test fixtures can be built. |
| 1006 | `streaming-claude-executor` | Port from v1: `StreamingClaudeRunner` Protocol + impl with `--output-format stream-json`, idle watchdog, process-group cleanup, env filtering. Tests inject a fake `subprocess.Popen`. | Every LLM call goes through this. Reuse v1 code; do not redesign. |
| 1007 | `claude-runner-protocol` | A higher-level `ClaudeRunner` Protocol over the streaming executor: `run(prompt, *, model, budget_usd, allowed_tools, idle_timeout_s, cwd) -> ClaudeResult`. Used by ingest/bootstrap/lint. Single seam tests mock. | Pulls policy (budget, --bare, --allowedTools) into one place so individual feature tasks don't repeat it. |
| 1008 | `git-cache` | Clone/fetch helpers under `.cadence-memory/git_cache/<repo>/`. `git clone --filter=blob:none` on first run, `git fetch origin <branch>` on subsequent. Pure subprocess wrappers; no Claude. | Worker depends on this. |
| 1009 | `git-walker` | `iter_pending_commits(repo, since_sha, branch, *, skip_patterns, noise_patterns) -> Iterator[IngestEvent]`. `IngestEvent` is either a single commit or a noise-batched group. `git log --reverse --topo-order` under the hood. | Pure logic, easy to test against a `tmp_path` git repo. |
| 1010 | `state-file` | `.cadence-memory/state.json` reader/writer. Atomic write via tmpfile + rename. Schema versioned (design2 §6.2). | Worker uses it on every iteration. |
| 1011 | `repos-management` | `cadence-memory repos add/list/remove`. ruamel.yaml round-trip; preserves comments. Friendly error on malformed config. | Convenience but a thin slice; ships visible CLI surface early so the user can drive subsequent tasks against real data. |
| 1012 | `ingest-prompt-and-flow` | `src/cadence_memory/defaults/prompts/ingest.txt` + `ingest_commit(event, wiki, runner)` orchestration: render prompt, call Claude, validate frontmatter on touched files, advance `state.json`, commit master-wiki diff. | The heart of the new design. Everything before this is plumbing. |
| 1013 | `worker-run` | `cadence-memory worker run [--mode commits] [--only REPO] [--limit N] [--dry-run]`. Wires git-cache + walker + state + ingest. POSIX file lock on `.cadence-memory/worker.lock`. | First end-to-end command. |
| 1014 | `worker-daemon` | `cadence-memory worker daemon`: long-running poll loop with `worker.poll_interval_s`. SIGTERM-safe between repos, SIGINT-safe between commits. `--once` is sugar for `worker run`. | After `worker run` proves out, the daemon is just a loop. |
| 1015 | `bootstrap-stages` | `src/cadence_memory/defaults/prompts/bootstrap-{1..5}.txt` (data-model, routes, architecture, gaps, plans). `cadence-memory bootstrap <repo>` (alias for `worker run --mode bootstrap --only <repo>`). Stack detection step at the top of each stage. | Independent of commit-walking; some users will start here. Do after `worker run` so the same plumbing is reused. |
| 1016 | `manual-ingest` | `cadence-memory ingest <path>`: same prompt scaffolding as commit-ingest, but the source is a file under `raw/notes/` (or anywhere in the wiki tree). Used by the wiki-ingest skill. | Bridge for sources that aren't git commits (articles, meeting notes). |
| 1017 | `lint-command` | `cadence-memory lint [--apply] [--only REPO]`: orphan pages, broken wikilinks, contradictions, missing pages, gaps refresh. Default writes a `lint/<date>` branch; `--apply` commits to current branch. | Maintenance layer; only useful once a wiki has real content. |
| 1018 | `query-command` | `cadence-memory query <text>`: `qmd` if available (subprocess), else `rg --type md` over the wiki. `--format json|table`. | Read-side surface. Trivial once everything else is in. |
| 1019 | `status-command` | `cadence-memory status [--short]`: list of repos, last_sha, last_run_at, pending commits, last_failure. `--short` is the form consumed by the SessionStart hook. | Tiny but unblocks the SessionStart hook task. |
| 1020 | `claude-skills` | Ship `wiki-researcher.md` and `wiki-ingest.md` under `src/cadence_memory/defaults/skills/`. `init` copies them into `<wiki>/.claude/skills/`. Update `init` to also write `.claude/settings.json` with the SessionStart hook (`cadence-memory status --short && head -60 index.md && tail -15 log.md`) and the `.git/hooks/post-commit` template. | Final integration with Claude Code. Depends on `status --short` and on the wiki layout being stable. |
| 1021 | `qmd-postcommit-hook` | Post-commit hook in the master wiki repo: `qmd index . --collection master` if qmd is on `$PATH`, else no-op. Installed by `init`. Idempotent on re-run. | Keeps search current after every commit (worker, lint, manual). |
| 1022 | `release-1.0.0` | Bump `__version__` to `0.4.0`. CHANGELOG entry summarizing the rewrite. PyPI publish + Homebrew formula update (per current CLAUDE.md release flow). End-to-end check from a clean install. | The cut-over commit. |

### Optional / v2.1 follow-ups (not in the initial pass)

- **Cross-project synthesis** — a periodic pass that mines `projects/*/` for repeated patterns and updates `patterns.md` / `learnings.md` cohesively (rather than incrementally on each ingest). Probably its own command (`cadence-memory synthesize`) with a dedicated prompt.
- **Conflict semantics for manual edits** — sharper rules than "preserve and flag in gaps.md" (e.g. detect three-way merges in the wiki when ingest disagrees with a recent manual edit).
- **Auth for private repos** — token-based fetch (PAT in env or per-repo deploy keys) for users whose `git` credentials aren't already wired up.
- **Force-push recovery** — auto-detect when `origin/<branch>` no longer contains `state.json[repo].last_sha` and reset to the merge-base, or surface a structured error with a one-command fix.
- **Cost dashboard** — sum `ClaudeResult.cost_usd` per repo / per day from the streaming executor, surface in `status` and `log.md`.

---

## 3. Notes for the LLM writing the init files

Read `docs/design2.md` end-to-end before drafting any init file. Cite the relevant section in `## Goal` (e.g. "implements design2 §7"). When in doubt about a detail, the design doc is authoritative; if the design doc itself is unclear, raise it with the user before writing the init — don't invent an answer.

Code samples in init files should compile against the modules that already exist. Before pasting a `from cadence_memory.foo import Bar` into an init, check the module is in scope (a previous task created it). If it isn't, add the dependency under `## Depends on` and rework the example.

Never include `Co-Authored-By` trailers in commit messages drafted inside init files. Commit messages follow the format from project `CLAUDE.md`: `<branch-name>. <Clause>: <what>.` on a single line.

Each task ends green: `pytest`, `ruff`, `mypy --strict` all pass. If a step would temporarily break the build (e.g. introducing a Protocol whose first impl lands a task later), restructure the task so it doesn't.
