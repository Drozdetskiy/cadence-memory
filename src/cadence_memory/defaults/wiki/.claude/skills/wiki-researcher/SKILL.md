---
name: wiki-researcher
description: Read-only wiki lookup skill. Reads the wiki catalog and recent activity, searches for relevant pages, and synthesizes a "Past Knowledge" section covering relevant pages, applicable patterns, known gotchas, reusable components, and open gaps — without modifying the wiki.
---

You are operating on a cadence-memory master wiki: an LLM-maintained knowledge base of wikilinked markdown pages kept current by a background worker.

## Steps

1. Read the wiki catalog and recent activity:
   - Run `head -60 index.md` to get the catalog summary.
   - Run `tail -15 log.md` to see what the worker ingested most recently.

2. Search for content relevant to the current task or question:
   - If the `qmd` MCP tool is available, use it: `qmd search <keywords> --collection master`.
   - Otherwise fall back to `cadence-memory query <keywords>`.
   - If neither is available, use `rg --type md <keywords>` in the wiki root.

3. Open the top hits in full and read them before synthesizing.

## Output

Return a **Past Knowledge** section containing exactly these five subsections. Omit a subsection only if it is genuinely empty (say so explicitly rather than leaving it out silently).

### Relevant pages
List each page that bears directly on the task, one bullet per page with a one-sentence summary and the wikilink (e.g. `[[projects/project-a/models/User]]`).

### Applicable patterns
Any cross-project patterns from `patterns.md` or per-project overviews that apply to the current task.

### Known gotchas
Hard-won lessons from `learnings.md` or project pages that the implementer should watch out for.

### Reusable components
Services, models, or utilities already in the wiki that the task could use instead of reimplementing.

### Open gaps
Entries in `gaps.md` (global or per-project) that the current task could resolve or partially address.

## Constraints

- Do NOT modify any wiki file. This skill is read-only.
- Do NOT write to `gaps.md`, `log.md`, or any page — even if you spot an error or a contradiction. Surface it in chat only.
- If the wiki has no prior knowledge on this topic, say: "The wiki has no prior knowledge on this topic." and return empty subsections.
