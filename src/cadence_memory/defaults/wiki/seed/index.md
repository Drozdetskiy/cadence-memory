---
title: "Wiki Catalog"
type: overview
project: _master
created: {date}
updated: {date}
tags: []
confidence: high
---

**TLDR**: Full catalog of this master wiki. The cadence-memory worker keeps this page current — adding entries as new pages appear and pruning entries for deleted pages.

## Cross-project root pages

- [[index]] — this page
- [[log]] — chronological audit of every ingest event
- [[gaps]] — open questions, contradictions, and TODOs
- [[decisions]] — cross-project ADRs (created lazily on first ingest)
- [[patterns]] — patterns seen in 2+ projects (created lazily)
- [[learnings]] — gotchas and hard-won lessons (created lazily)

## Projects

_No projects tracked yet._ Add a repo via `cadence-memory repos add <name> <url>` and run `cadence-memory worker run` to populate `projects/<name>/`.

## Raw drop-zone

- `raw/notes/` — paste manually-curated notes, articles, or meeting minutes here, then run `cadence-memory ingest raw/notes/<file>` to fold them into the wiki.
