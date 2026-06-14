---
name: wiki-distill
description: Populate the cross-project decisions.md / patterns.md / learnings.md root pages from recurring findings across existing wiki pages — decisions made, patterns seen in 2+ pages, and hard-won gotchas — preserving each root page's frontmatter and the page schema.
---

You are operating on a cadence-memory master wiki. The three cross-project root
pages exist to capture knowledge that recurs across the tree:

- **`decisions.md`** — cross-project ADRs (architecture/strategy decisions made).
- **`patterns.md`** — patterns observed in **2 or more** pages.
- **`learnings.md`** — gotchas and hard-won lessons.

This skill harvests those from the existing pages and folds them into the three
root pages without disturbing the per-page content they came from.

## Steps

1. **Inventory the tree.** Read `head -80 index.md` for the catalog, then read
   the substantive pages (overviews, decisions, architecture, gaps, and project
   subtrees). You are looking for three kinds of recurring signal:
   - **Decisions** — a choice was made and justified (e.g. "we standardized on
     X", "rejected Y because Z"). One decision per page or across pages.
   - **Patterns** — the SAME approach, structure, or trade-off appears in **2+**
     pages. A one-off is not a pattern; require at least two independent
     occurrences and cite both.
   - **Learnings** — a gotcha, a footgun, a "we discovered the hard way" note.

2. **Read the three root pages' current state and frontmatter.** Note their
   existing `created:` date (preserve it) and their structure. If a root page
   does not yet exist, it is created lazily — author it with full frontmatter
   (`type: decision` for `decisions.md`, `type: pattern` for `patterns.md`,
   `type: overview` for `learnings.md`; `project: _master`).

3. **Distill entries.** For each root page, write tight, self-contained entries.
   Every entry MUST link back to the page(s) it was distilled from with
   `[[wikilinks]]`, so the root page stays traceable:

   - `decisions.md` entry: the decision, the rationale, the alternatives
     rejected, and `[[source page]]` links. ADR-style is ideal (Context /
     Decision / Consequences).
   - `patterns.md` entry: the pattern, where it shows up (**cite the 2+ pages**),
     and when to apply vs avoid it.
   - `learnings.md` entry: the gotcha, why it bites, and how to avoid it, with
     `[[source page]]` links.

   Do not duplicate an entry that is already on the root page — merge/update
   instead. Append new entries; refresh stale ones; keep the page deduplicated.

4. **Preserve frontmatter and schema.** Edit only the body below the frontmatter
   block. Keep the original `created:` date; bump `updated:` to today
   (`YYYY-MM-DD`). Do not change `type` / `project` / `title`. The
   `confidence:` field, if present, stays `high | medium | low`.

5. **Validate** each edited root page with the installed parser before
   committing (it must still parse):

   ```sh
   CMPY=$(ls -d /opt/homebrew/Cellar/cadence-memory/*/libexec/bin/python 2>/dev/null | sort -V | tail -1)
   for f in decisions.md patterns.md learnings.md; do
     "$CMPY" - "$f" <<'PY'
   import sys
   from pathlib import Path
   from cadence_memory.documents.frontmatter import parse_page
   p = parse_page(Path(sys.argv[1]))
   print("VALID:", sys.argv[1], "->", p.frontmatter.type, p.frontmatter.confidence)
   PY
   done
   ```

   (Note the explicit inline list `decisions.md patterns.md learnings.md` — zsh
   does not word-split a variable.)

6. **Catalog and log.** Make sure each root page is listed in `index.md`
   (they usually are, under the cross-project root section). Append a `log.md`
   line summarizing what was distilled and from which pages.

7. **Publish** per the `wiki-sync` commit/push step (push only if a remote
   exists).

## What counts as a pattern

A pattern requires **2 or more** independent occurrences across pages — cite all
of them. If you can only find one occurrence, it belongs in the originating page
or as a learning, NOT in `patterns.md`. This is the single most common mistake
when distilling.

## Gotchas (must respect)

- Edit only the **body** of each root page — never touch its frontmatter block
  except to bump `updated:` to today's `YYYY-MM-DD` date.
- `confidence:` is `high | medium | low` ONLY; dates are `YYYY-MM-DD`; required
  keys are `title, type, project, created, updated, tags, confidence`; `type` is
  one of `model | service | controller | architecture | decision | pattern |
  overview | log | gaps`.
- The shell is **zsh** — explicit inline lists in loops, never `for x in $VAR`.
- A pattern needs **2+** cited occurrences; a single instance is not a pattern.
- Validate every edited page with `cadence_memory.documents.frontmatter.parse_page`
  before committing.

## Constraints

- Distill from existing pages; do NOT invent decisions, patterns, or learnings
  that are not supported by the tree.
- Do NOT modify the source pages you distill from — only the three root pages.
- Surface contradictions in `gaps.md`; never silently resolve them.
- Hand-edits to these root pages are respected by the worker — do not expect a
  later ingest to overwrite your distillation, and likewise do not clobber a
  user's prior hand-edits; merge.
