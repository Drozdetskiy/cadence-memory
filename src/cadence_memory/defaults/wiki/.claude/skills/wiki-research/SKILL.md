---
name: wiki-research
description: Given a research brief, run a multi-agent research->synthesize->verify->persist pipeline that produces sourced notes in raw/notes/, then ingests each into the master wiki. Decomposes the brief into deliverables with 2-3 research facets each, authors a background Workflow with the proven fan-out/fact-check pipeline, and folds the verified output into wiki pages.
---

You are operating on a cadence-memory master wiki. This skill turns a free-form
research brief into verified, sourced wiki pages by running a background
multi-agent pipeline and then ingesting its output.

**Opt-in note:** background `Workflow`s require explicit user opt-in. Invoking
this skill IS that opt-in — you may author and run a `Workflow` as part of this
skill without asking again.

## Steps

1. **Read the brief and the wiki.** Restate the user's research brief in one
   sentence. Run `head -60 index.md` and `tail -15 log.md` so the new notes
   complement existing pages instead of duplicating them.

2. **Decompose into deliverables.** Break the brief into 2-5 *deliverables* —
   each becomes ONE wiki note. Give every deliverable 2-3 *research facets*
   (orthogonal sub-questions). One facet = one parallel research agent. This
   fan-out is what gives the notes breadth without any single agent going
   shallow.

3. **Write a `SCOPE` preamble** shared by every agent. It MUST instruct agents
   to:
   - Use `WebSearch` and `WebFetch` (and, if those tools are not available, to
     call `ToolSearch` with `"select:WebSearch,WebFetch"` first).
   - Prefer independent / primary sources over vendor marketing.
   - Distinguish **FACT** (with a source URL) from **ANALYSIS / inference**,
     mark confidence, and flag single-source or anecdotal claims as such.
   - Be concrete: real names, dates, numbers, URLs.
   - Output clean markdown with **no YAML frontmatter and no enclosing code
     fence** (frontmatter is added at ingest time, not by the research agents).

4. **Author a background `Workflow`** using the template below. The proven
   pipeline is four stages:
   `parallel facets -> synth (merge) -> verify (independent fact-check) -> persist (write the note to raw/notes/ via a persist agent)`.
   Use `agentType:'general-purpose'` and a `schema` for every structured agent
   so output is machine-readable. The verify stage is a *separate, independent*
   agent that re-checks the synthesized note against sources — never let the
   author grade its own work.

5. **Run the Workflow.** It writes one `raw/notes/<slug>.md` per deliverable.

6. **Ingest each produced note** by invoking the `wiki-ingest` skill (or running
   `cadence-memory ingest raw/notes/<slug>.md`) once per note. For notes that
   return `no changes (0 pages)` — common for cross-cutting / synthesis notes —
   fall back to the hand-fold procedure in the `wiki-sync` skill.

7. **Report**: list each note, its path, the verifier's confidence, and whether
   it ingested as a page or was hand-folded.

## Copy-pasteable Workflow template

Distilled from the proven research scripts. Replace `NOTES_DIR`, `SCOPE`, and
`DELIVERABLES`; leave the schemas, `assemble()`, `persistPrompt()`, and the
`pipeline(...)` call as-is.

```js
export const meta = {
  name: 'wiki-research',
  description: 'Fan-out research -> synth -> verify -> persist notes into raw/notes',
  phases: [
    { title: 'Research',    detail: 'parallel facet research per deliverable' },
    { title: 'Synthesize',  detail: 'merge facets into one note per deliverable' },
    { title: 'Verify',      detail: 'independent fact-check of each note' },
    { title: 'Persist',     detail: 'write each note to raw/notes' },
  ],
}

// Absolute path to THIS wiki's raw/notes drop-zone.
const NOTES_DIR = '/ABSOLUTE/PATH/TO/WIKI/raw/notes'

const SCOPE = [
  'CONTEXT: <one or two lines: what this wiki is for and what this pass adds>. Today is <YYYY-MM-DD>.',
  '',
  'METHOD:',
  '- Use WebSearch and WebFetch. If not available, call ToolSearch with "select:WebSearch,WebFetch" first.',
  '- Prefer independent/primary sources over vendor marketing. Be concrete: names, dates, numbers, URLs.',
  '- Distinguish FACT (source URL) from ANALYSIS/inference; mark confidence; flag single-source/anecdotal claims.',
  '',
  'OUTPUT: clean markdown, no YAML frontmatter, no enclosing code fence.',
].join('\n')

// One object per wiki note. facets[] = parallel research agents.
const DELIVERABLES = [
  {
    slug: 'example-deliverable',
    title: 'Human-Readable Note Title',
    angle: 'Why this note exists / the gap it fills.',
    facets: [
      { key: 'facet-a', prompt: 'Focused research question A. Be specific about what to find and which sources to prefer.' },
      { key: 'facet-b', prompt: 'Focused research question B.' },
    ],
  },
]

const FACET_SCHEMA = {
  type: 'object',
  required: ['findings_markdown'],
  properties: {
    findings_markdown: { type: 'string', description: 'clean markdown findings for this facet; no frontmatter' },
    key_points: { type: 'array', items: { type: 'string' } },
    sources: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, url: { type: 'string' } } } },
  },
}

const NOTE_SCHEMA = {
  type: 'object',
  required: ['title', 'summary', 'body_markdown'],
  properties: {
    title: { type: 'string' },
    summary: { type: 'string' },
    body_markdown: { type: 'string', description: 'merged, well-structured markdown; no frontmatter' },
    sources: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, url: { type: 'string' } } } },
    open_questions: { type: 'array', items: { type: 'string' } },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['overall_confidence', 'corrections_markdown'],
  properties: {
    overall_confidence: { type: 'string' },
    overclaimed_points: { type: 'array', items: { type: 'string' } },
    corrections_markdown: { type: 'string' },
  },
}

function assemble(note, v) {
  const p = []
  p.push('# ' + (note.title || 'Untitled'))
  p.push('')
  if (note.summary) { p.push('**Summary:** ' + note.summary); p.push('') }
  p.push(note.body_markdown || '')
  if (v && (v.corrections_markdown || (v.overclaimed_points || []).length)) {
    p.push(''); p.push('## Verification & caveats')
    if (v.overall_confidence) p.push('_Verifier confidence: ' + v.overall_confidence + '_')
    if (v.corrections_markdown) { p.push(''); p.push(v.corrections_markdown) }
    if ((v.overclaimed_points || []).length) {
      p.push(''); p.push('**Points the verifier judged over-claimed / single-source:**')
      v.overclaimed_points.forEach(c => p.push('- ' + c))
    }
  }
  if ((note.open_questions || []).length) {
    p.push(''); p.push('## Open questions')
    note.open_questions.forEach(q => p.push('- ' + q))
  }
  if ((note.sources || []).length) {
    p.push(''); p.push('## Sources')
    note.sources.forEach(s => p.push('- ' + (s.title ? (s.title + ' - ') : '') + (s.url || '')))
  }
  return p.join('\n')
}

function persistPrompt(path, markdown) {
  return [
    'Write the content below to the file at this absolute path, using the Write tool: ' + path,
    'Write it EXACTLY as given. Do not edit, summarize, reformat, translate, or add commentary.',
    'After writing, reply with only the word: done',
    '',
    '----- BEGIN CONTENT -----',
    markdown,
    '----- END CONTENT -----',
  ].join('\n')
}

phase('Research')
const results = await pipeline(
  DELIVERABLES,
  // 1. Research: fan out one agent per facet, in parallel.
  (d) => parallel(d.facets.map(f => () => agent(
    SCOPE + '\n\n# Deliverable: ' + d.title + '\n' + d.angle + '\n\n## Your facet: ' + f.key + '\n\n' + f.prompt,
    { label: 'research:' + d.slug + ':' + f.key, phase: 'Research', schema: FACET_SCHEMA, agentType: 'general-purpose' },
  ))).then(rs => ({ d, facets: rs.filter(Boolean) })),
  // 2. Synthesize: merge the facets into one coherent note.
  (x) => {
    if (!x || !x.facets.length) return null
    return agent(
      SCOPE + '\n\nMerge these research facets into ONE coherent wiki note for "' + x.d.title + '".\n' + x.d.angle + '\n\n'
      + x.facets.map((f, i) => '## Facet ' + (i + 1) + '\n' + (f.findings_markdown || '')).join('\n\n') + '\n\n'
      + 'Produce a single well-structured note (## / ### headings): de-duplicate, keep the concrete facts and source URLs, list open_questions. No frontmatter.',
      { label: 'synth:' + x.d.slug, phase: 'Synthesize', schema: NOTE_SCHEMA, agentType: 'general-purpose' },
    ).then(note => ({ d: x.d, note }))
  },
  // 3. Verify: an INDEPENDENT agent re-checks the note against sources.
  (y) => {
    if (!y) return null
    return agent(
      'You are an independent fact-checker. Use WebSearch/WebFetch (ToolSearch "select:WebSearch,WebFetch" if needed).\n\n'
      + 'Note title: ' + y.note.title + '\nSummary: ' + (y.note.summary || '') + '\n\nFull note:\n' + (y.note.body_markdown || '') + '\n\n'
      + 'Verify the load-bearing claims (figures, dates, named entities, outcomes). Flag any unsupported, single-source, or likely-hallucinated. Provide corrections_markdown with fixes and source URLs. Set overall_confidence.',
      { label: 'verify:' + y.d.slug, phase: 'Verify', schema: VERIFY_SCHEMA, agentType: 'general-purpose' },
    ).then(v => ({ d: y.d, note: y.note, markdown: assemble(y.note, v), confidence: v.overall_confidence }))
  },
  // 4. Persist: a write-only agent drops the assembled note into raw/notes.
  (z) => {
    if (!z) return null
    return agent(persistPrompt(NOTES_DIR + '/' + z.d.slug + '.md', z.markdown),
      { label: 'persist:' + z.d.slug, phase: 'Persist', agentType: 'general-purpose' })
      .then(() => ({ slug: z.d.slug, title: z.note.title, path: NOTES_DIR + '/' + z.d.slug + '.md', confidence: z.confidence }))
  },
)

return { notes: results.filter(Boolean), note_count: results.filter(Boolean).length }
```

## After the Workflow: ingest

For each `raw/notes/<slug>.md` the Workflow wrote, ingest it (one note per
invocation):

```
cadence-memory ingest raw/notes/<slug>.md
```

Watch the output for two cases:
- `no changes (0 pages)` — the ingest pass judged the note redundant. For
  synthesis / cross-cutting notes this is common; hand-fold it using the
  `wiki-sync` procedure.
- a `FAILED` frontmatter error and revert — fix per the gotchas below and retry.

## Gotchas (must respect)

- The research/synth/verify agents emit **NO frontmatter** — frontmatter is
  added at ingest time. If you ever hand-author frontmatter, the `confidence:`
  field accepts ONLY `high | medium | low` (a value like `medium-high` is a hard
  ingest FAILURE + revert), and `created` / `updated` must be `YYYY-MM-DD` dates
  (not datetimes). Required keys: `title, type, project, created, updated, tags,
  confidence`; `type` is one of `model | service | controller | architecture |
  decision | pattern | overview | log | gaps`.
- The shell is **zsh** — it does NOT word-split unquoted variables. To loop over
  notes, write an explicit inline list (`for n in a b c; do ...; done`), never
  `for n in $NOTES`.
- `cadence-memory ingest` runs a fresh model pass; it often returns
  `no changes (0 pages)` for synthesis notes it judges redundant. Use the
  `wiki-sync` hand-fold fallback for those.
- Validate any hand-authored frontmatter with the installed parser (see
  `wiki-sync`).

## Constraints

- One note per deliverable; one parallel research agent per facet.
- The verify agent must be independent of the synth agent (never grade your own
  work).
- Do NOT hand-rewrite ingested wiki pages — let ingest (or the documented
  hand-fold fallback) own page creation.
