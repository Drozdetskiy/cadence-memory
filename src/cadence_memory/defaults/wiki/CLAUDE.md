# Master wiki — schema & query protocol

This repository is a cadence-memory **master wiki**: an LLM-maintained knowledge
base of `[[wikilinked]]` markdown pages, kept current by a background worker
that walks new commits in tracked source repos. See `docs/design2.md` in the
cadence-memory source for the full design.

**Always check `index.md` and the `projects/` tree before answering questions about this knowledge base's contents.**

## Tree

```
config.yaml                  the only hand-edited config
index.md                     full catalog (LLM-maintained)
log.md                       append-only chronological audit
gaps.md                      open questions, contradictions, TODOs
decisions.md                 cross-project ADRs
patterns.md                  patterns seen in 2+ projects
learnings.md                 gotchas & hard-won lessons
projects/<repo-slug>/        per-source-repo subtree
  overview.md
  data-model.md
  architecture.md
  routes.md
  decisions.md
  gaps.md
  models/<entity>.md
  services/<service>.md
raw/notes/                   manual drop-zone for articles, meeting notes
```

## Page conventions

Every wiki page begins with a YAML frontmatter block. The schema is mechanically
validated; pages that fail validation are a hard error.

```yaml
---
title: "User"
type: model              # one of: model | service | controller | architecture |
                         #          decision | pattern | overview | log | gaps
source: app/models/user.rb
project: project-a       # source-repo slug, or "_master" for cross-project pages
created: 2026-05-08
updated: 2026-05-08
tags: [auth, billing]
confidence: high         # high | medium | low
---

**TLDR**: One sentence summary that previews what the page contains.

(rest of the page here, with [[wikilinks]] to other pages)
```

### Wikilinks

Use `[[Page Name]]` for cross-references. For pages nested under a project,
use the path-form: `[[projects/project-a/models/User]]`.

There is no separate backlinks index — Claude resolves wikilinks by reading
`index.md` and grepping the tree.

## Query protocol

When asked about this wiki's contents:

1. Read `index.md` (head) and `log.md` (tail) for the catalog and recent activity.
2. Search via the `qmd` MCP if available, otherwise `cadence-memory query <text>`
   or `rg --type md`.
3. Read the most relevant pages in full before answering.
4. If pages contradict each other, surface the contradiction explicitly and
   add an entry to `gaps.md` rather than silently picking one.

## Maintenance

The cadence-memory worker writes pages, the `index.md` catalog, and the `log.md`
audit on every commit it ingests. It does NOT touch `raw/` (manual drop-zone)
and respects hand-edits — disagreements between an ingest pass and a manual
edit are recorded in `gaps.md`, never silently overwritten.

When the wiki contradicts code you are reading, file an entry in `gaps.md` —
never silently rewrite the page.

## Operations

| Command | Description |
|---------|-------------|
| `cadence-memory worker run` | Walk pending commits and ingest them via Claude. |
| `cadence-memory lint` | Audit the master wiki for orphans, broken links, contradictions, and missing pages. |
| `cadence-memory ingest <path>` | Ingest a single non-commit source (article, meeting notes, spec) into the master wiki. |
| `cadence-memory status` | Print at-a-glance worker state for every tracked repo. |
