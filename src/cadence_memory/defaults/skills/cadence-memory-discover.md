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
