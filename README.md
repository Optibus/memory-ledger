# work-brain

An external working memory for a **busy** brain — a graph-linked local memory
plus a set of skills you drive in plain language (any language), from **Slack,
Claude Code, or the Claude app**. You talk; it remembers, breaks work into tiny
next-steps, sets real alarms, and reaches your other tools.

## The idea

Everything you tell it becomes a **node** in a graph (a person, task,
requirement, decision, note, reminder, writing sample…) connected by **edges**
(`assigned-to`, `part-of`, `depends-on`, `about`…). Nothing lives only in your
head, and everything is one link away from its context.

Storage is a single **SQLite** file — fast to query, trivial to back up, and
exportable to readable Markdown or JSON any time.

The brain is **global and lives outside any repo**, at `~/.work_brain` (override
with `$WORK_BRAIN_HOME`), so every project on your machine shares one brain and
it's never committed:

```
~/.work_brain/
  graph.db            the memory graph (one portable file)
  learned_skills/     skills the brain grew at runtime (one folder + SKILL.md each)
  exports/            export-memory output
  (configs, routines, definitions live here too)
```

**Finished work is archived, never deleted** — `complete` a task and it moves to
the done archive: out of your active views, but kept, searchable, and still
linked so the brain can learn from what worked.

**The brain grows (neuroplasticity).** When a repeatable procedure shows up, it
`learn`s a new skill — writing it to `learned_skills/` and recording *when to use
it*. Next time a matching situation appears, it recalls the skill (associative
match on the triggers), follows it, and `reinforce`s it so proven skills surface
first. Learnings are global, so a skill learned in one project applies in another.

## Designed around four needs

1. **Externalize memory** — capture everything, resurface it unprompted.
2. **Micro-steps** — every task is auto-split into tiny, startable actions.
3. **Time-awareness support** — due dates, estimates, and *real* alarms that
   fire on their own and DM you.
4. **Reduce overwhelm** — replies are one calm, clear next action, never a wall.

These are enforced in `CONVENTIONS.md`, which every skill reads first.

## Skills

| Skill | What it does |
|---|---|
| `remember` | Capture info into the graph as linked nodes. |
| `recall` | Retrieve / resurface context ("what's on my plate", "what's due"). |
| `add-task` | Create a task **and** break it into micro-steps. |
| `complete-task` | Mark a task done → move to the archive (never delete). |
| `learn-skill` | Grow a new skill at runtime, recall it, reinforce it (neuroplasticity). |
| `visualize` | Open a live, interactive map of the whole graph in the browser. |
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

The DB path resolves as: `$WORK_BRAIN_DB` → `$WORK_BRAIN_HOME/graph.db` →
`~/.work_brain/graph.db` (default). Same in Claude Code and the Claude app.
See `CONVENTIONS.md` §2.

```bash
python3 scripts/graph.py complete --id <id> --note "shipped"   # archive, don't delete
python3 scripts/graph.py add-skill --name triage-x --title "..." --when "..." --body "..."
python3 scripts/graph.py match-skills "a situation"            # associative recall
python3 scripts/graph.py use-skill --id skill_triage-x         # reinforce
```

## Visualize the brain

A dependency-free local server renders the live graph — force-directed layout,
zoom/pan, search, type filters, and click-to-inspect showing every connection
and its direction. It polls the DB so changes appear in real time.

```bash
python3 viz/serve.py            # opens http://127.0.0.1:8770/ in your browser
python3 viz/serve.py --no-open --port 8771 &   # background, custom port
```

Binds to localhost only — nothing leaves the machine.

## Install

**Claude Code** — add as a plugin (the folder with `.claude-plugin/plugin.json`).
Then talk to it with `/brain ...` or invoke skills by name.

**Claude app** — upload the skill folders as Agent Skills. The same graph
engine runs in the sandbox; keep memory portable via `~/.work_brain/` or a
connected persistent location.

## Connected machines

Reminders/alarms use scheduled routines; Calendar, Gmail, Slack, and Jira use
their MCP connectors. Reading is free; anything outward (send, create, schedule)
is confirmed once before it happens.
