---
name: wiki-ingest
description: Ingests a file from raw/notes/ into the master wiki by calling `cadence-memory ingest <path>`. Reports the commit SHA and pages touched. Does not hand-edit raw files or rewrite wiki pages directly.
---

You are operating on a cadence-memory master wiki. This skill integrates a manual drop from the `raw/notes/` drop-zone into the wiki's structured pages.

## Steps

1. Confirm the target path is under `raw/notes/`. If the user provides a path outside `raw/notes/`, ask them to move or copy the file there first — do not ingest from an arbitrary path.

2. Call the ingest command:
   ```
   cadence-memory ingest <path>
   ```
   where `<path>` is the path to the file the user pointed you at.

3. Wait for the command to complete. On success it prints the commit SHA and a list of wiki pages that were created or updated. Relay that summary to the user.

4. On failure, relay the error message from the command. Do not attempt to fix the wiki pages manually.

## Constraints

- Do NOT edit the raw file. It stays in `raw/notes/` as-is after ingest.
- Do NOT hand-rewrite or patch wiki pages yourself. The ingest command runs Claude with the authoritative ingest prompt; manual rewrites bypass validation and may corrupt frontmatter.
- Do NOT run `cadence-memory ingest` more than once for the same file in a single session unless the user explicitly requests a retry.
