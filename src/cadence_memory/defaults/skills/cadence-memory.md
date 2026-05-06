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
Returns chunks (sections of documents). Each result has `chunk_id`,
`document_id`, `kind`, `title`, `project`, `heading_path`, `slug`, `snippet`.

## Read a specific document or chunk
`cadence-memory get <document_id>`  — raw markdown of the full document.
`cadence-memory get <document_id>#<slug>`  — body of a single chunk (use
the `chunk_id` returned by `query`).

## Check ephemeral (short-term task context)
`cadence-memory ephemeral list --format json`
Always check this first — it usually contains what the user is currently
working on.

## Workflow for planning tasks
1. `ephemeral list` to see current task context.
2. `query` for relevant services, patterns, ADRs.
3. `get <chunk_id>` for the matched section, or `get <document_id>` for
   the whole file when broader context is needed.
4. Synthesize a plan that respects the existing patterns.
