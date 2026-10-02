# Usage: the ingest and the report

"Where the Quota Went" (`scripts/usage_report.py`) is drawn from `usage-week`
nodes. `scripts/usage_ingest.py` writes them, from Claude Code's own local
session logs, with no model call and no network. That is the point: when you are
out of quota you can still see where it went.

## What it reads, and keeps

It reads `~/.claude/projects/*/*.jsonl` (and each session's `subagents/` logs);
`--projects-dir`, `$MEMORY_LEDGER_USAGE_PROJECTS_DIR` or `$CLAUDE_CONFIG_DIR`
point it elsewhere. From each record it takes only token counts, timestamps, tool
names and file paths. **No prompt text, answer text or file contents are stored**
in the nodes or in the checkpoint.

| Metric (`props.metrics`) | From |
|---|---|
| `costUSD`, `byModel`, `composition` (input, output, cacheWrite, cacheRead), `cacheReadTokens` | token usage x the price table |
| `sessions`, `turns`, `activeDays` | one turn per distinct API message (streamed duplicates count once) |
| `typedPrompts` | user records that are not tool results, subagent messages or system notes |
| `durationBuckets`, `noCompactionSessions` | session span; a compaction boundary in the log |
| `byRepo`, `editsByRepo`, `workingDirs`, `tickets` | `Edit`/`Write`/`NotebookEdit` targets, resolved to the repository by walking up to `.git` (a linked worktree folds onto its repository); a ticket key such as `ABC-123` read off a worktree directory name |
| `noWriteSessions`, `noWriteOutward*`, `investigation*` | sessions that edited no repo file, and the outward actions (MCP writes, `gh` writes, brain writes) they made |
| `rateLimits` | rejected-quota records and "limit reached" messages |
| `costByHour`, `costByWeekday` | the local hour and weekday of each turn (the report's "When it happens" section) |

A session belongs to the ISO week, in the brain's `tz` setting (else UTC), in
which it started.

## Cost is an estimate

Logs carry tokens, not dollars. Each turn is priced from `PRICES` in
`scripts/usage_ingest.py`: published list price per million tokens (input, output,
cache write, cache read) per model family, one-hour cache writes at 2x input. It is
raw per-token pricing, not what a subscription charges: read it as relative weight
and quota burn. Override or extend it with a JSON file, `--prices FILE` or
`$MEMORY_LEDGER_USAGE_PRICES`:

```json
{"some-new-model": [3.0, 15.0, 3.75, 0.3]}
```

Keys are regexes on the model id, values `[input, output, cache_write, cache_read]`.
A model nothing matches is priced at a default and listed in the week's
`unpricedModels`. Changing the table re-prices every stored session on the next run.

## Running it

```bash
python3 scripts/usage_ingest.py run [--since YYYY-MM-DD] [--dry-run] [--json] [--force]
python3 scripts/usage_report.py render --ingest --out usage.html   # ingest, then draw
python3 scripts/usage_ingest.py status
```

`--since` snaps to the ISO week containing the date. `--dry-run` writes nothing,
not even the checkpoint.

**Automatically:** a `SessionStart` hook (`hooks/usage_ingest.py`, `startup` only)
starts a **detached** `usage_ingest.py run` at most once every six hours and
returns at once. The hook itself only checks one stamp file and forks: it never
parses a log, never touches the graph, never waits, prints nothing, and always
exits 0, so a broken ingest cannot block a session. A lock file keeps two runs from
overlapping. It leaves a switched-off or not-yet-created brain alone. Opt out with
`MEMORY_LEDGER_NO_USAGE_INGEST=1` or `"usage_ingest": false` in
`<home>/settings.json`. The CLI always works when asked.

## Idempotent, incremental, never deleting

- The node id is `usage-week_<YYYY-Www>`. A rerun updates in place and never
  duplicates; a week whose metrics did not change is not written at all. Writes go
  through `graph.py`'s helpers, so `_after_write()` and the neo4j outbox run.
- `<home>/usage_ingest.json` keeps each log's mtime and size with a small summary,
  so a rerun re-parses only files that changed; the rest is one `stat()` each. The
  summaries hold counts and names only. A change of price table, timezone or parser
  version invalidates it. It is written only after the nodes are.
- A week is never overwritten with fewer sessions than it holds (Claude Code
  prunes old logs, and a lost checkpoint must not shrink history), nor when another
  tool wrote it, unless `--force`.

## Known limits

- A session's whole cost lands in the week it started.
- A resumed session that Claude Code copies into a new log file is counted in both
  files; there is no cross-file de-duplication.
- Repository attribution needs the path to still exist on disk; edits to deleted
  worktrees, or outside any git repository, are not attributed to a repository.
- Files changed through `Bash` (redirects, `sed -i`) are not seen as edits.
- The insights ccalyze writes (what to change, and why) are not reproduced here.

## The Agents-tab meter

`GET /api/usage-summary` feeds the meter under the conversation list in the Agents tab: estimated
dollars for the last 5 hours and today on the bar, and week, month, year and total
when it is clicked open. It sums `metrics.costByAbsHour` (cost per local hour,
written by the ingest) over the `usage-week` nodes, and starts a detached ingest
when the last one is over ten minutes old. A week written by another tool without
hour buckets counts whole on its first day (`coarseWeeks`). These are list-price
estimates, not the subscription quota.
