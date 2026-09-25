# Query methods

Three ways to read the brain, in increasing cost order. **Pick the cheapest one
that answers the question** — that rule is the whole cost story.

| Path | Model call | Token cost | Use when |
|---|---|---|---|
| `brain_query.py` | never | zero in a terminal; only the rows you read in a session | date views, "what's on my plate" |
| `graph.py` | never | only the rows you read | search, recall, graph walks, writes |
| `/ledger`, skills | yes | a normal prompt | you need judgement, or to reach Slack/Gmail/Jira |

Both scripts are stdlib-only and print JSON with `--json` / by default, so they
compose with `jq` and with each other.

---

## `brain_query.py` — date views

Read-only, no writes, no model. Seven views:

```bash
python3 scripts/brain_query.py agenda      # overdue + today + tomorrow
python3 scripts/brain_query.py open        # ALL open tasks, dated or not
python3 scripts/brain_query.py today
python3 scripts/brain_query.py tomorrow
python3 scripts/brain_query.py week        # next 7 days (+ overdue)
python3 scripts/brain_query.py closed --since-days 7
python3 scripts/brain_query.py range --from 2026-07-20 --to 2026-07-31 --status open
```

### Ownership filters

`--mine` is **strict** — only tasks whose `assignee` is the owner
(`$SECOND_BRAIN_OWNER`, default `Owner`), never unassigned, never someone
else's, and never delegated or parked waiting/blocked. It answers "what must *I*
act on", which is a different question from "what is open".

```bash
python3 scripts/brain_query.py agenda --mine
python3 scripts/brain_query.py open --assignee me|unassigned|"<name>"
```

### Realm filter

`--realm` scopes any view to one or more domains (comma list). Omit it for every
realm. Realms are open vocabulary — whatever you have written with `--realm` is
what you can filter by, so nothing has a fixed list of them, including the web UI.

```bash
python3 scripts/brain_query.py agenda --realm personal
python3 scripts/brain_query.py open --realm work,shared --mine
```

A node with no realm counts as the configured default (`$SECOND_BRAIN_REALM`,
`default` out of the box) — the same convention the engine applies on write, so
filtering never silently drops untagged nodes.

### Kind filter

`--kind` scopes by `props.kind` — free-form and sparse, used by skills for
`learned` / `plugin` and by notes for whatever the author needed. Also open
vocabulary: read it from `/api/kinds` or `brain_query.kinds()`.

```bash
python3 scripts/brain_query.py open --kind learned
python3 scripts/brain_query.py agenda --kind "(none)"    # carrying no kind
```

Most nodes have no kind, so they get an explicit `(none)` bucket instead of being
invisible to the filter.

### Other flags

| Flag | Effect |
|---|---|
| `--realm` | comma list of realms to keep (see above) |
| `--kind` | comma list of `props.kind` values to keep (see above) |
| `--tz` | IANA timezone for day bucketing (default `$SECOND_BRAIN_TZ`, then the `tz` setting in `<home>/settings.json`, else system) |
| `--backend sqlite\|neo4j\|auto` | read the local mirror or the neo4j master |
| `--json` | machine-readable instead of the text table |
| `--status open\|done\|any` | `range` only |

**Timezone matters.** Due dates are stored as ISO strings with offsets, and are
compared as parsed datetimes, never lexically — `07:20-03:00` is *later* than
`08:00+00:00`, so string comparison silently misorders mixed-offset data.

---

## `graph.py` — the engine

### Reading

```bash
python3 scripts/graph.py recall "ops driver depot"    # associative, ranked, budgeted
python3 scripts/graph.py search "confluence"          # lexical FTS5, budgeted
python3 scripts/graph.py get-node --id <id>           # one node in full + its edges
python3 scripts/graph.py neighbors --id <id> --depth 2
python3 scripts/graph.py list --type task --status open
python3 scripts/graph.py due
python3 scripts/graph.py open-loops --stale-days 7
python3 scripts/graph.py stats
python3 scripts/graph.py export
```

**`recall` vs `search`** — `search` is lexical only: it returns what literally
matches. `recall` seeds on the same index then spreads across edges and reranks by
memory strength, so it also surfaces the ticket's PR, its blocker, and who it is
assigned to. Prefer `recall` for questions, `search` when you know the term.

Key `recall` flags:

| Flag | Default | Effect |
|---|---|---|
| `--depth` | 2 | hops activation spreads |
| `--budget` | 1600 | max chars of payload (the token guard) |
| `--limit` | | max rows |
| `--all` | off | include done/cancelled |
| `--include-captures` | off | include raw prompt captures |
| `--no-wire` | off | skip Hebbian edge creation (makes it a pure read) |

`search` takes `--budget` (default 2600), `--type`, `--limit`, and `--full` to
bypass trimming entirely.

### Writing

```bash
python3 scripts/graph.py add-node --type task --title "..." \
  --body "..." --props '{"priority":"P1"}' --status open \
  --due 2026-08-04T18:00:00-03:00 --realm work --area hub_process

python3 scripts/graph.py update-node --id <id> --props '{"assignee":"Alex"}'
python3 scripts/graph.py add-edge --src <a> --dst <b> --rel blocked-by
python3 scripts/graph.py complete --id <id>          # -> archive, never deleted
python3 scripts/graph.py set-state --id <id> --status blocked
python3 scripts/graph.py del-node --id <id>          # real delete; prefer complete
```

`--props` merges rather than replaces. `--area` adds the `part-of` edge to a
domain hub and errors if the hub does not exist, so a typo cannot silently
orphan a node.

### Genome and skills

```bash
python3 scripts/graph.py match-rules "write a slack reply"
python3 scripts/graph.py match-skills "triage a failing PR"
python3 scripts/graph.py use-skill --id <id>         # reinforces
python3 scripts/graph.py add-skill --title "..." --body "..."
python3 scripts/graph.py ingest-conventions          # CONVENTIONS.md -> rule nodes
python3 scripts/graph.py ingest-skills
python3 scripts/graph.py genome
python3 scripts/graph.py sections | areas | add-area
```

### Maintenance and context

```bash
python3 scripts/graph.py consolidate         # digest the day's captures
python3 scripts/graph.py maintain            # housekeeping pass
python3 scripts/graph.py suggest-links       # propose edges, don't create them
python3 scripts/graph.py park "<context>"    # move context out of the window
python3 scripts/graph.py resume              # pull the thread back
python3 scripts/graph.py unpark --id <id>
python3 scripts/graph.py flush               # drain the neo4j outbox
python3 scripts/graph.py neo4j-status
```

---

## Budgets

Every read path that could return a lot returns a *budgeted* payload instead:

| Path | Default budget | Override |
|---|---|---|
| `recall` | 1600 chars | `--budget` |
| `search` | 2600 chars | `--budget`, or `--full` |
| `match-rules` / `match-skills` | 2600 chars | `--budget` |

Budgeted responses carry `chars`, `budget`, `of` (how many matched) and
`truncated` (how many bodies were clipped), plus a `hint` naming the command that
returns the full text. So a caller can always tell it is seeing a summary, and
knows how to drill in — which is what stops it from re-querying blindly.

Uncapped, `search` returned whole bodies — ~9,000 chars where `recall` answered
the same cue in ~1,500.

---

## Reading from the dashboard instead

Every view above is also served over HTTP by `scripts/serve.py`, and the dashboard
can write too (due dates, status, assignee, relations) without spending a token.
See [the API reference](api.md).

---

## Exit codes and errors

Both scripts print a JSON object with `ok: true|false`. On failure, `error`
carries the message. Nothing writes to stderr on the happy path, so
`| jq` is always safe.
