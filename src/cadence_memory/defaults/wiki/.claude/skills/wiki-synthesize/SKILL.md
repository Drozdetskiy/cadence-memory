---
name: wiki-synthesize
description: Generate or refresh a top-level capstone synthesis page that consolidates the wiki's scattered conclusions into one actionable page — a verdict/thesis plus open decisions plus a requirements/criteria checklist. Written as a normal note in raw/notes/ then ingested, or hand-folded if the ingest pass no-ops it.
---

You are operating on a cadence-memory master wiki. Over time a wiki accumulates
many narrow pages but no single place that states the bottom line. This skill
produces (or refreshes) ONE capstone page that a decision-maker can read on its
own.

## Steps

1. **Survey the wiki.** Read `head -80 index.md` for the full catalog and
   `tail -20 log.md` for recent activity. Identify the pages that carry
   conclusions, trade-offs, and open questions (overviews, decision pages,
   gaps, and the cross-project root pages `decisions.md` / `patterns.md` /
   `learnings.md` / `gaps.md`).

2. **Read the load-bearing pages in full** — not just their TLDRs. The capstone
   must reflect what the pages actually conclude, with the caveats intact. If
   two pages contradict each other, surface the contradiction in the capstone
   and add an entry to `gaps.md` rather than silently picking a side.

3. **Decide the capstone's shape.** Pick the `type` that fits:
   - a strategic bottom-line / go-no-go page -> `type: decision`
   - a state-of-the-world consolidation -> `type: overview`
   The page is cross-project, so `project: _master`.

4. **Draft the capstone as a normal note** in `raw/notes/<slug>.md` with **no
   frontmatter** (ingest adds it). Structure it as an actionable page:

   - **Verdict / thesis** — the one-paragraph bottom line the scattered pages
     add up to. State confidence and the single biggest uncertainty.
   - **What we know** — the firmest, best-sourced conclusions, each with a
     `[[wikilink]]` to its source page.
   - **Open decisions** — the choices that are NOT empirical but are the
     reader's to make. Lay out the options and trade-offs; do NOT pick for
     them.
   - **Requirements / criteria checklist** — concrete, checkable conditions that
     would have to hold for the thesis to succeed (or be falsified). Write them
     as a checklist so the reader can mark each.
   - **Cross-references** — `[[wikilinks]]` to every page the capstone draws on.

   Keep it tight and actionable — this is a decision surface, not a re-dump of
   the underlying pages.

5. **Ingest it.** Invoke the `wiki-ingest` skill or run:

   ```sh
   cadence-memory ingest raw/notes/<slug>.md
   ```

6. **If ingest returns `no changes (0 pages)`** — which is COMMON for capstone /
   synthesis notes, because the fresh ingest pass often judges a cross-cutting
   summary redundant with the pages it summarizes — fall back to the hand-fold
   procedure in the `wiki-sync` skill: prepend validated frontmatter
   (`type: decision` or `overview`, `project: _master`, `YYYY-MM-DD` dates,
   `confidence: high|medium|low`), `cat` the body, validate with the installed
   `parse_page` parser, add an `index.md` catalog entry, and append a `log.md`
   line.

7. **Publish** per `wiki-sync` step 5 (commit; push if a remote exists).

## Refreshing an existing capstone

If the capstone page already exists, re-run the survey, then update the raw note
and re-ingest. If ingest no-ops, hand-fold by overwriting the existing page's
body below its frontmatter (keep the original `created:` date, bump `updated:`).

## Gotchas (must respect)

- The raw note carries **no frontmatter**; frontmatter is added at ingest or
  hand-fold time. `confidence:` is `high | medium | low` ONLY; `created` /
  `updated` are `YYYY-MM-DD` dates; required keys are `title, type, project,
  created, updated, tags, confidence`; `type` is one of the allowed enum values
  (a capstone is normally `decision` or `overview`).
- **Capstone notes very frequently no-op on ingest** — plan for the hand-fold
  fallback; it is the expected path, not a failure.
- The shell is **zsh** — explicit inline lists in loops, never `for x in $VAR`.
- Validate hand-authored frontmatter with the installed parser
  (`cadence_memory.documents.frontmatter.parse_page`, see `wiki-sync`).

## Constraints

- Synthesize from existing pages; do NOT invent new facts here. If a needed fact
  is missing, note it as a gap rather than fabricating it — use `wiki-research`
  to go get it.
- Surface contradictions; never silently resolve them. File them in `gaps.md`.
- One capstone page per invocation.
