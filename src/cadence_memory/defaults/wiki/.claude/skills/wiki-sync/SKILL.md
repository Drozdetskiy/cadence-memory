---
name: wiki-sync
description: Ingest all pending raw/notes/*.md into the master wiki and publish. Enumerates notes, runs cadence-memory ingest per note, hand-folds any note that returns "no changes" into a validated page, updates index.md and log.md, optionally lints, then commits and pushes if a remote exists.
---

You are operating on a cadence-memory master wiki. This skill drains the
`raw/notes/` drop-zone into structured wiki pages and publishes the result.

## Steps

### 1. Enumerate pending notes

List the notes explicitly — the shell is **zsh**, which does NOT word-split an
unquoted variable, so `for n in $NOTES` silently iterates once over the whole
string. Always use an inline literal list or glob:

```sh
ls raw/notes/*.md
```

Then loop with an **explicit inline list** of the slugs you intend to process:

```sh
for n in note-a note-b note-c; do
  echo "=== $n ==="
  cadence-memory ingest "raw/notes/$n.md"
done
```

(Do NOT write `for n in $NOTES; do ...` — under zsh that is one iteration.)

### 2. Ingest each note and classify the result

For each note, read the `cadence-memory ingest` output and bucket it:

- **Created/updated pages** — success. Note the commit SHA and page list.
- **`no changes (0 pages)`** — the fresh ingest pass judged the note redundant
  (very common for SYNTHESIS / cross-cutting notes). If the note is important,
  hand-fold it (step 3).
- **`FAILED` + revert** — almost always a frontmatter error. The most common
  cause is a bad `confidence:` value or a datetime where a date is required.
  Fix and retry (step 3 gotchas).

### 3. Hand-fold a note that returned "no changes"

Only do this for notes the user cares about keeping. The procedure turns a raw
note (which has NO frontmatter) into a valid wiki page by prepending frontmatter
and appending the body verbatim.

1. **Pick a slug and page metadata.** Choose `type` from
   `model | service | controller | architecture | decision | pattern | overview | gaps`
   (synthesis pages are usually `overview`, `architecture`, or `decision`).
   Cross-project pages use `project: _master`.

2. **Build the page** by prepending frontmatter, then a `**TLDR**:` line, then
   the raw note body. Write the frontmatter with a heredoc and append the body
   with `cat` so the body is byte-for-byte preserved:

   ```sh
   SLUG=my-note
   {
     printf -- '---\n'
     printf 'title: "%s"\n' "My Note Title"
     printf 'type: overview\n'
     printf 'source: raw/notes/%s.md\n' "$SLUG"
     printf 'project: _master\n'
     printf 'created: 2026-01-01\n'   # YYYY-MM-DD only
     printf 'updated: 2026-01-01\n'
     printf 'tags: [tag-a, tag-b]\n'
     printf 'confidence: high\n'      # high | medium | low ONLY
     printf -- '---\n\n'
     printf '**TLDR**: %s\n\n' "One-sentence preview of the page."
     cat "raw/notes/$SLUG.md"
   } > "$SLUG.md"
   ```

   Place the page where it belongs (repo root for `_master` pages, or
   `projects/<slug>/...` for project pages). If the raw note already starts with
   a `# Heading` you may drop it to avoid a duplicate title, but otherwise keep
   the body unchanged — fold, do not rewrite.

3. **Validate the frontmatter with the installed parser.** Find the interpreter
   that ships with the installed CLI (the Homebrew Cellar libexec venv), then
   parse the page — this is the SAME validator the ingest pipeline uses:

   ```sh
   CMPY=$(ls -d /opt/homebrew/Cellar/cadence-memory/*/libexec/bin/python 2>/dev/null | sort -V | tail -1)
   # (Fallback: `head -1 "$(command -v cadence-memory)"` shows the shebang interpreter.)
   "$CMPY" - "$SLUG.md" <<'PY'
   import sys
   from pathlib import Path
   from cadence_memory.documents.frontmatter import parse_page
   p = parse_page(Path(sys.argv[1]))
   print("VALID:", p.frontmatter.title, p.frontmatter.type, p.frontmatter.confidence)
   PY
   ```

   `parse_page` takes a `Path` (not a string of text). A bad `confidence:` value
   prints e.g. `confidence: must be one of ['high', 'medium', 'low'], got
   'medium-high'`. Fix any reported error before continuing.

4. **Catalog the page in `index.md`.** Add one bullet under the appropriate
   section, matching the existing entry style: `- [[slug]] — one-line
   description of what the page covers.`

5. **Append a `log.md` line** recording the hand-fold, matching the existing
   log format:

   ```
   ## [YYYY-MM-DD] manual | <slug>.md — hand-folded after ingest no-op

   The ingest pass returned "no changes" (judged redundant with sibling pages).
   Page hand-created from raw/notes/<slug>.md with validated frontmatter and
   catalogued in index.md. Source note preserved in raw/notes/.
   ```

### 4. Optional lint

```sh
cadence-memory lint
```

Surfaces orphans, broken wikilinks, contradictions, and missing pages. Fix
anything cheap; report the rest.

### 5. Commit and publish

Stage everything and commit with a conventional message. Push only if a remote
exists:

```sh
git add -A
git commit -m "docs(wiki): ingest pending notes and hand-fold synthesis pages"
if git remote -v | grep -q .; then
  git push origin "$(git branch --show-current)"
else
  echo "No remote configured — committed locally only."
fi
```

## Gotchas (must respect)

- **zsh does not word-split** — always loop with an explicit inline list, never
  `for x in $VAR`.
- **`confidence:` accepts only `high | medium | low`.** `medium-high` (or any
  other value) is a hard ingest FAILURE that reverts the page.
- **`created` / `updated` must be `YYYY-MM-DD` dates**, not datetimes.
- **Required frontmatter keys:** `title, type, project, created, updated, tags,
  confidence`. `type` is one of `model | service | controller | architecture |
  decision | pattern | overview | log | gaps`.
- **Ingest no-ops are normal for synthesis notes** — the hand-fold fallback is
  the intended path, not a workaround for a bug.
- **Validate with the installed parser** (`cadence_memory.documents.frontmatter.parse_page`)
  before committing a hand-folded page.

## Constraints

- Do NOT edit the raw note bodies — they stay in `raw/notes/` as-is. Hand-folding
  copies the body verbatim into a new page; it does not rewrite the source.
- Prefer `cadence-memory ingest` first; hand-fold only the notes it no-ops that
  the user wants kept.
- Never force-push.
