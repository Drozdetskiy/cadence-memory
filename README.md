# cadence-memory

An LLM-authored knowledge base for Claude Code. You point cadence-memory at a list of git repositories and a master-wiki repo. A background worker clones each repo, walks its history one commit at a time, and asks Claude to update the wiki — entity pages, architecture overviews, ADRs, an `index.md`, a `log.md`. After catching up, the worker pulls each repo's tracked branch periodically and continues to extend the wiki as new commits land. You read the wiki in any markdown editor; Claude reads it via a SessionStart hook and via the [`qmd`](https://github.com/tobi/qmd) search MCP. The wiki is the artifact, not an SQLite index.

## Install

```bash
pip install cadence-memory
```

```bash
brew install drozdetskiy/cadence/cadence-memory
```

## Quickstart

```bash
cadence-memory init          # create a new master-wiki repo and configure it
cadence-memory repos add <path>   # track a source repo
cadence-memory worker run    # catch up the wiki with all tracked repos
cadence-memory query <text>  # search the wiki
```

## Worker lock

A `cadence-memory worker run` or `worker daemon` invocation acquires a POSIX advisory lock on `<wiki>/.cadence-memory/worker.lock`. The lock file is removed when the worker exits, so do not check for the file's existence to determine whether a worker is running — it will be absent both before the first run and after every clean exit. Shell scripts that need to serialize bootstrap runs across multiple repos should probe liveness with `flock -n <wiki>/.cadence-memory/worker.lock true` (a non-zero exit means a worker currently holds the lock).

## Design

See [`docs/design2.md`](docs/design2.md) for the full architecture.

## License

MIT — see [LICENSE](LICENSE).
