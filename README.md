# second-brain

An external working memory for a **busy** brain — a graph-linked local memory
plus a set of skills you drive in plain language (any language), from **Slack,
Claude Code, or the Claude app**. You talk; it remembers, breaks work into tiny
next-steps, sets real alarms, and reaches your other tools.

> **Install:** see [INSTALL.md](INSTALL.md) — `/plugin marketplace add` this repo,
> `/plugin install second-brain@second-brain`, then `mkdir -p ~/.second_brain`.

## The idea

Everything you tell it becomes a **node** in a graph (a person, task,
requirement, decision, note, reminder, writing sample…) connected by **edges**
(`assigned-to`, `part-of`, `depends-on`, `about`…). Nothing lives only in your
head, and everything is one link away from its context.

Storage is a single **SQLite** file — fast to query, trivial to back up, and
exportable to readable Markdown or JSON any time.

The brain is **global and lives outside any repo**, at `~/.second_brain` (override
with `$SECOND_BRAIN_HOME`), so every project on your machine shares one brain and
it's never committed:

```
~/.second_brain/
  graph.db            the memory graph — nodes, edges, AND skills (all live here)
  learned_skills/     optional local SKILL.md exports (only if add-skill --with-file)
  exports/            export-memory output
  neo4j.json          optional neo4j master config (secret — never commit)
  (routines, definitions live here too)
```

**Finished work is archived, never deleted** — `complete` a task and it moves to
the done archive: out of your active views, but kept, searchable, and still
linked so the brain can learn from what worked.

**neo4j master + sections.** The unified source of truth is a **neo4j** graph
(config in `~/.second_brain/neo4j.json`); the local SQLite is a durable mirror.
Every write goes to both — and if neo4j is down it's **queued and auto-synced**
when it's back (`neo4j-status`, `flush`, `scripts/brain_sync.py`). Every node
carries a **`realm` / section** (`optibus`, `personal`, …) so one graph holds
work + personal life, filterable (`sections`, `add-node --realm`). The
visualizer reads from neo4j too.

**The brain grows (neuroplasticity).** When a repeatable procedure shows up, it
`learn`s a new skill — stored **as a node in the graph** (triggers + procedure in
the DB, synced everywhere; not an isolated file). Next time a matching situation
appears, it recalls the skill (ranked by relevance, reinforcement as a booster),
follows it, and `reinforce`s it so proven skills surface first. Learnings are
global, so a skill learned in one project applies in another. (`ingest-skills`
pulls any existing markdown skills into the DB as nodes.)

## Designed around four needs

1. **Externalize memory** — capture everything, resurface it unprompted.
2. **Micro-steps** — every task is auto-split into tiny, startable actions.
3. **Time-awareness support** — due dates, estimates, and *real* alarms that
   fire on their own and DM you.
4. **Reduce overwhelm** — replies are one calm, clear next action, never a wall.

These are enforced in `CONVENTIONS.md`, which every skill reads first.

**Always-on capture — token-free.** When the brain is enabled, plugin hooks
persist every interaction in pure Python/SQL (no model, no tokens): a
`UserPromptSubmit` hook (`hooks/capture_prompt.py`) stores each prompt and
auto-creates tasks for explicit to-dos; a `Stop` hook (`hooks/capture_answer.py`)
stores a summary of the answer, linked to its question. So a simple answered
question becomes a recallable node on its own — and you recall it for free via
`graph.py search` / `brain_query.py`, never spending tokens to "remember".
Disable it by removing/renaming the brain home (`~/.second_brain`); the hooks then
no-op.

## Skills

| Skill | What it does |
|---|---|
| `remember` | Capture info into the graph as linked nodes. |
| `ingest-context` | Ingest a Google Meet transcript, Slack thread, email, or plain text into the graph (auto-ingests meeting-transcript emails). |
| `recall` | Retrieve / resurface context ("what's on my plate", "what's due"). |
| `add-task` | Create a task **and** break it into micro-steps. |
| `complete-task` | Mark a task done → move to the archive (never delete). |
| `follow-up` | Surface every open loop (tasks, PRs, emails, messages) and drive it toward closed. |
| `learn-skill` | Grow a new skill at runtime, recall it, reinforce it (neuroplasticity). |
| `visualize` | Open the live brain UI in the browser — graph **map** + query **dashboard** (two tabs). |
| `remind` | Set a real alarm (scheduled routine) that DMs you on Slack. |
| `calendar` | Read/create Google Calendar events; time-block tasks. |
| `read-email` | Read & triage Gmail; turn action items into tasks. |
| `send-slack` | Send/schedule a Slack message in your voice. |
| `slack-inbox` | Read Slack and route each message to the right skill. |
| `read-jira` | Read Jira issues/JQL; cache them into the graph. |
| `web-search` | Search the web; save useful findings as resources. |
| `delegate` | Hand a task to a person/agent and track the follow-up. |
| `writing-style` | Learn your voice and write as you, in your language. |
| `export-memory` | Dump the whole graph to Markdown / JSON. |

Front door in Claude Code: **`/brain <anything>`** routes to all of the above.
**`/plan-today`** picks up only *your* actionable tasks (token-free) and blocks
them into your calendar's free windows, ending with one clear first action.

## The graph engine

`scripts/graph.py` — a dependency-free CLI over SQLite (FTS5 search, graph
traversal, due-date queries, export). Skills call it; you can too:

```bash
python3 scripts/graph.py add-node --type task --title "Ship report" --due 2026-07-23T09:00:00+00:00
python3 scripts/graph.py search "report"
python3 scripts/graph.py due --within-days 7
python3 scripts/graph.py neighbors --id <node-id> --depth 2
python3 scripts/graph.py export --format both
```

The DB path resolves as: `$SECOND_BRAIN_DB` → `$SECOND_BRAIN_HOME/graph.db` →
`~/.second_brain/graph.db` (default). Same in Claude Code and the Claude app.
See `CONVENTIONS.md` §2.

```bash
python3 scripts/graph.py complete --id <id> --note "shipped"   # archive, don't delete
python3 scripts/graph.py add-skill --name triage-x --title "..." --when "..." --body "..."  # node-first (--with-file for a local .md)
python3 scripts/graph.py ingest-skills                         # pull markdown skills into the DB as nodes
python3 scripts/graph.py match-skills "a situation"            # recall: relevance-ranked, reinforcement-boosted
python3 scripts/graph.py use-skill --id skill_triage-x         # reinforce
```

## Token-free views (`brain_query.py`)

Everyday reads don't need the model. `scripts/brain_query.py` answers the common
date views in pure SQL — **no tokens** — as a text table or `--json`:

```bash
python3 scripts/brain_query.py agenda      # overdue + today + tomorrow
python3 scripts/brain_query.py open        # ALL open tasks (dated or not) — best with --assignee
python3 scripts/brain_query.py week        # next 7 days (+ overdue)
python3 scripts/brain_query.py closed --since-days 7
python3 scripts/brain_query.py range --from 2026-07-20 --to 2026-07-31 --status open
python3 scripts/brain_query.py agenda --mine   # only what YOU must act on
```

Add **`--mine`** to any view (dashboard: the "🙋 Mine · needs action" toggle) to
drop tasks assigned to someone else, delegated away, or parked waiting/blocked —
leaving only the owner's live actions (`$SECOND_BRAIN_OWNER`, default `Gabriel`;
unassigned counts as the owner's). Or filter by exact owner with
**`--assignee me|unassigned|<name>`** (dashboard: one toggle chip per assignee).
`--mine` is **strict**: only tasks whose `assignee` is the owner (not unassigned,
not someone else), and never delegated or parked waiting/blocked. Rows carry
**blocked_by / blocks / related** flags from the graph edges (dashboard shows
🔒/⛓/🔗 badges), cards **expand on click** for the full body + linked neighbors
(`/api/node`), and you can **set a due date right in the dashboard** — click a
card's date chip → date picker (writes token-free via `/api/set-due`). Captured
to-dos get their **assignee inferred** (mine / a named person / unassigned — never
blindly "me"; "ask/chase <Name>" stays *yours*).
Local-tz buckets via `--tz` / `$SECOND_BRAIN_TZ`; `--backend sqlite|neo4j|auto`.
Recall stays free — reach for this (or `graph.py search` / `open-loops`) instead
of asking the model to remember.

## Visualize the brain

A dependency-free local server serves one page with **two tabs**:

- **Map** — the live force-directed graph: zoom/pan, search, type filters, and
  click-to-inspect showing every connection and its direction. Polls the DB so
  changes appear in real time.
- **Dashboard** — the `brain_query.py` views (agenda / open / today / tomorrow /
  week / closed / range) as cards, over the same store.

```bash
python3 viz/serve.py            # opens http://127.0.0.1:8770/ in your browser
python3 viz/serve.py --no-open --host 0.0.0.0 --port 8770 &   # reachable on LAN / phone
```

Reads neo4j when reachable, else the SQLite mirror. Binds to localhost by default;
use `--host 0.0.0.0` (behind a tunnel) to reach it from your phone. Read-only.

## Install

See **[INSTALL.md](INSTALL.md)** for the full flow. In short:

**Claude Code** — install as a plugin (marketplace add this repo →
`/plugin install second-brain@second-brain`), then `mkdir -p ~/.second_brain`. Enabling
at user level makes it active in every session. Talk to it with `/brain ...` or
invoke skills by name.

**Claude app** — upload the skill folders as Agent Skills. The same graph engine
runs in the sandbox; keep memory portable via `~/.second_brain/` or a connected
persistent location.

## Connected machines

Reminders/alarms use scheduled routines; Calendar, Gmail, Slack, and Jira use
their MCP connectors. Reading is free; anything outward (send, create, schedule)
is confirmed once before it happens.
