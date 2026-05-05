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
