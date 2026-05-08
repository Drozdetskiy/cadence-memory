# cadence-memory

LLM-maintained knowledge base for Claude Code. A worker walks new commits in tracked source repos and asks Claude to keep a single master-wiki repo current. Search via `qmd` MCP. See [`docs/design2.md`](docs/design2.md) for the design and [`docs/features.md`](docs/features.md) for the implementation order.

## Status

The v1 codebase has been wiped (task `0032-fresh-start`). v2 implementation has not started yet — `src/` and `tests/` are absent until task `0001-package-skeleton` lands. Everything below this section applies to v2 (when it exists) and to the v1-style branch/commit/release conventions which carry over unchanged.

When working on v2 tasks, **read `docs/design2.md` end-to-end before drafting any cadence init file.** Cite the relevant section in `## Goal` (e.g. "implements design2 §7"). The design is authoritative; if a detail is unclear, raise it with the user before writing the init.

## Implementation tasks

Work is sliced into atomic tasks under `cdc-tasks/` — one `init` file per task, executed in order via cadence (`cadence --plan` → `--task`, or `--run --impl --squash`). Each task ends with `make check` passing. The task list and the base init template live in [`docs/features.md`](docs/features.md). Numbering restarts from `0001` for v2 (the v1 sequence ended at `0032-fresh-start`).

## Coding conventions

- Python 3.14+, `mypy --strict`. No `Any`; all Protocol boundaries annotated.
- **Protocol-based interfaces** for every external dependency (`ClaudeRunner`, `GitClient`, `WikiStore`, etc.). Tests mock the Protocol, not the concrete impl.
- **No `rich`** — manual column/textwrap layout in any formatter code.
- Embedded defaults under `src/cadence_memory/defaults/` are read via `importlib.resources` — never hard-coded paths.
- Dataclasses: `@dataclass(frozen=True, slots=True)` for configs and DTOs.
- No global mutable state; everything passed as parameters.
- Every Claude prompt template lives under `src/cadence_memory/defaults/prompts/` and is loaded via `importlib.resources`. No inline f-string prompts in business logic.
- Frontmatter on wiki pages is mechanically validated. Bad frontmatter is a hard error, not a warning.

## Testing patterns

- Mock the `ClaudeRunner` Protocol everywhere — **never invoke a real `claude` subprocess in CI**. The streaming executor's tests inject a fake `subprocess.Popen`.
- For git operations: use `tmp_path` + `subprocess.run(["git", "init"], ...)`; do not hit network. Wrap `git` calls behind a `GitClient` Protocol so unit tests can mock without touching disk.
- `tmp_path` for everything filesystem-related: configs, wiki fixtures, ingest fixtures.
- Typer `CliRunner` for CLI command tests.

## Build & run

For package operations (build, install, publish, dependency management) always use `pdm` — `pdm build`, `pdm add`, `pdm install`, `pdm publish`. Do NOT use raw `pip install`, `python -m build`, or other pip-based workflows; the project is configured around PDM (`pdm.lock`, `pdm-backend`).

Run tools directly from the project venv (`source venv/bin/activate`). Do NOT use `pdm run`.

```bash
pytest tests/ -v                # run tests
ruff check src/ tests/          # lint
ruff format src/ tests/         # format
mypy src/                       # strict type check
cadence-memory --version        # verify CLI
make check                      # lint + typecheck + test
```

These commands will fail until `0001-package-skeleton` reintroduces `src/`, `tests/`, and the project entry point.

## Branch and commit flow

Never commit directly on `main`. Every change — features, fixes, doc edits, version bumps — lands on a numbered feature branch named `<NNNN>-<slug>` (continuing the sequence visible in `git log`). The user pushes the branch and merges via GitHub PR. If you find yourself on `main` with edits to commit, create the branch first (`git switch -c <NNNN>-<slug>`).

## Commit messages

Format: a single line `<branch-name>. <Clause>: <what>.` where `<Clause>` is `Added`, `Changed`, or `Deleted`. English. No blank line, no multi-line body — the whole commit message is one line. The PR title will carry that line verbatim; expand on details in the PR description if needed.

A single commit can carry any combination of `Added`, `Changed`, and `Deleted` clauses, separated by `. ` (period + space). Within one clause, list multiple items separated by `; ` (semicolon + space). Always include only the clauses that apply.

Each item is **one short clause** in plain language describing the user-visible outcome — what someone reading `git log --oneline` cares about. Implementation details (method/test/file names, renames, formatter passes, doc syncs) belong in the diff, not the commit. When squashing, write a fresh summary — do not concatenate the sub-commit messages.

Good (single clause):
```
0005-frontmatter-parser. Added: parser that extracts YAML frontmatter and the document body for downstream indexing.
```

Good (multiple clauses, multiple items):
```
0012-discover-rerun. Added: --apply flag for discover; kind_rules support in config. Changed: discover writes proposed annotations to a sidecar file by default. Deleted: legacy --auto-apply alias.
```

Bad (verbose, name-listing, sub-commit concat): `0005-... Added: ParsedDocument dataclass, parse_text/parse_file functions, fixtures for CRLF and bad-yaml, regex for ---/--- delimiter, ...`

Author as the user — no `Co-Authored-By` trailer.

## Releasing a new version

The package is published as `cadence-memory` on PyPI; the Homebrew formula lives in [Drozdetskiy/homebrew-cadence](https://github.com/Drozdetskiy/homebrew-cadence) alongside the `cadence` formula and exposes the CLI as `cadence-memory`.

1. **Add a CHANGELOG.md entry** for the new version using the existing format (`## vX.Y.Z - YYYY-MM-DD`, then sections like New Features / Fixes / Other). Focus on user-visible changes since the previous tag — new commands, flags, behavior changes, fixes — not internal refactors.
2. **Bump version** in `src/cadence_memory/__init__.py` on a `<NNNN>-<slug>` branch (same branch as the changelog entry); merge to `main` via PR.
3. **Build and publish to PyPI**:
   ```bash
   rm -rf dist/ && pdm build
   python3 - <<'PY'
   import configparser, os, subprocess
   c = configparser.ConfigParser(); c.read(os.path.expanduser("~/.pypirc"))
   env = {**os.environ, "PDM_PUBLISH_USERNAME": "__token__", "PDM_PUBLISH_PASSWORD": c["pypi"]["password"]}
   subprocess.run(["pdm", "publish", "--repository", "pypi", "--no-build"], env=env, check=True)
   PY
   ```
   `--no-build` ensures the artifact whose `sha256` you'll paste into the formula is byte-identical to what PyPI serves.
4. **Tag and create a GitHub Release**:
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
   Then hand the user the prefilled URL `https://github.com/Drozdetskiy/cadence-memory/releases/new?tag=vX.Y.Z`, the title (`vX.Y.Z`), and the release body (the corresponding CHANGELOG.md section, copy-paste ready) — the user publishes the GitHub Release themselves via the web UI. Do NOT run `gh release create`.
5. **Update the Homebrew formula** in `homebrew-cadence/Formula/cadence-memory.rb`:
   - Replace `url` and `sha256` with the new sdist values from `https://pypi.org/pypi/cadence-memory/X.Y.Z/json` (look for the entry where `packagetype == "sdist"`).
   - **Only if `pyproject.toml` dependencies changed**, regenerate the `resource` blocks. `brew update-python-resources` cannot see packages newer than its internal PyPI snapshot, so resolve manually:
     ```bash
     python3.14 -m venv /tmp/r && /tmp/r/bin/pip install --dry-run --report /tmp/r.json cadence-memory==X.Y.Z
     ```
     Then for each resolved dependency fetch the sdist URL/sha256 from `https://pypi.org/pypi/<name>/<version>/json` and write the `resource "<name>" do … end` block.
   - Verify locally: `brew audit --strict drozdetskiy/cadence/cadence-memory && brew install --build-from-source drozdetskiy/cadence/cadence-memory && brew test drozdetskiy/cadence/cadence-memory`.
   - Commit, push.
6. **End-to-end check**: from a clean state — `brew untap drozdetskiy/cadence && brew tap drozdetskiy/cadence && brew install drozdetskiy/cadence/cadence-memory && cadence-memory --version`. Use the fully tap-qualified name (`drozdetskiy/cadence/cadence-memory`) so the install resolves to this tap unambiguously.

PyPI versions are immutable (no re-uploads under the same `X.Y.Z`); if anything goes wrong after step 3, bump the patch version and start again.
