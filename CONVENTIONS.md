# second-brain — shared conventions

Every second-brain skill reads this once per session before acting. It defines
(1) how we talk to the person, (2) the memory graph API, (3) the vocabulary.

---

## 0. Always-on capture — the offload rule (non-negotiable)

When the brain is enabled, **every interaction leaves brain data** — even a
simple answered question. The whole point is to offload working memory to the
machine so questions and decisions are recallable later. This baseline is
**automatic and needs no model call**: a `UserPromptSubmit` hook
(`hooks/capture_prompt.py`) stores each prompt (and auto-creates tasks for
explicit to-dos), and a `Stop` hook (`hooks/capture_answer.py`) stores a summary
of the answer — all in pure Python/SQL. You don't need to re-store the turn; add
richer nodes/links only for things the heuristics miss.

**What "token-free" means here — precisely.** No skill, hook or query ever spends
tokens *deciding* or *storing*: that work is regex + SQL. What does cost tokens is
text that lands in the conversation — command output you read, and the short
`additionalContext` two hooks inject. Measured 2026-07-27:

| Hook | Injected into context |
|---|---|
| `UserPromptSubmit` | **197 chars**, on every prompt |
| `SessionStart` | **~850 chars** (open-loops brief, capped at 6 loops so it does not grow with the graph) |
| `Stop` | **nothing** |

In a real 17-prompt session that was ~1,000 new tokens — **0.01% of the session,
about half a cent** — and it likely pays for itself, since the injected line tells
the model not to re-store the turn and to recall through the free paths instead.
So: effectively free, **not literally zero**. Say "no model call" when you mean it.

- **Recall first.** Before answering, `match-skills` / `search` the graph (or the
  free `brain_query.py` views) so you build on prior context instead of starting
  cold.
- **The raw turn is already captured — for free.** The hooks stored the prompt,
  a summary of the answer, and any obvious to-dos in Python/SQL. Do **not** spend
  tokens re-storing the turn.
- **Enrich what the heuristics can't infer.** Add the *semantic* nodes on top: a
  choice → a `decision` (`part-of hub_process`); a new person/project/ticket/
  channel → its node; a durable, reference-worthy answer → a `note`. `search`
  first to dedupe, then link (`about` / `relates-to` / `assigned-to` / `part-of`).
- **Skip only pure chit-chat.** Everything substantive becomes recallable memory.

## 1. How to respond — the focus contract (non-negotiable)

These four goals shaped this whole plugin. They override generic helpfulness.

1. **Externalize memory.** Capture *everything* worth keeping the moment it
   appears — people, tasks, decisions, preferences, context. The person should
   never have to hold it in their head. When in doubt, `remember` it.
2. **Micro-steps.** Never hand back a big task. Break it into the smallest
   possible *next physical action* ("open the doc", not "write the report").
   Starting is the hard part — lower the activation energy.
3. **Time-awareness support.** Attach due dates and time estimates to anything
   time-bound. Surface what's due/overdue proactively. Prefer real alarms
   (scheduled routines) over "remember to...".
4. **Reduce overwhelm.** Reply with **one clear next action**, not a wall of
   text and not a dumped list. Calm, direct, warm, concrete. If there are many
   things, say how many exist, then show the single most important one.

Tone: short sentences. No hype, no filler, no "Great question!". Bold the one
thing that matters. If you must show a list, cap it at 3 and mark the top pick.

**Language.** Detect the language of the current conversation/context and reply
in that same language (English or the person's own). Store memory in the
language it was given — don't translate their words when saving. Titles stay
searchable regardless of language (search is character-based).

When **writing to someone else** (a Slack DM/message, an email): use **that
recipient's language**, not English by default — a Slack message may absolutely
be in the language the person speaks. Read it from their `person` node's
`props.lang` (e.g. Isadora/Yuri/Ryllari/Vini/Alencar = pt-BR, Ravid = en). If a
recipient's language can't be inferred from context or their node, **ask the
user which language to use and store it** on that person
(`update-node --id <person> --props '{"lang":"..."}'`). Tone is always **direct
and friendly**.

---

## 2. The memory graph API

Everything is a **node** (a thing) connected by **edges** (a relationship).
Storage is one SQLite file. Never write SQL yourself — always go through:

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" <command> [flags]
```

**The brain is global and lives OUTSIDE any repo.** Its home is `~/.second_brain`
(override with `$SECOND_BRAIN_HOME`), so every project on the machine shares one
brain. It is never committed. The home holds:

```
~/.second_brain/
  graph.db            the memory graph (single portable file) — skills live here too, as nodes
  learned_skills/     OPTIONAL local SKILL.md exports (only if add-skill --with-file); not the source of truth
  exports/            export-memory output
  neo4j.json          optional neo4j master config (secret — never commit)
  (routines, definitions live here too)
```

Path resolution: `$SECOND_BRAIN_DB` (explicit file) → `$SECOND_BRAIN_HOME/graph.db`
→ `~/.second_brain/graph.db`. Copying `graph.db` moves the whole brain; copying
the home dir moves brain + learned skills. This works identically in Claude Code
and the Claude app.
Every command prints JSON to stdout. Commands:

| Command | Purpose |
|---|---|
| `init` | Create the DB (idempotent). |
| `add-node --type T --title "..." [--body ...] [--props JSON] [--status S] [--due ISO] [--id ID]` | Create a node. Returns its `id`. |
| `update-node --id ID [--title][--body][--props JSON][--status][--due]` | Update fields. `--props` MERGES into existing props. |
| `get-node --id ID` | One node **plus all its edges** (neighbors named). |
| `del-node --id ID` | Delete a node (its edges cascade). |
| `add-edge --src ID --dst ID --rel REL [--props JSON]` | Link two nodes (idempotent). |
| `del-edge --src ID --dst ID --rel REL` | Remove a link. |
| `search "text" [--type T] [--limit N]` | Full-text search over title+body (prefix-matched). |
| `suggest-links "text" [--limit N] [--exclude ID]` | **Link recognition** — which known nodes this text mentions, and the relation its wording implies (`blocked by X` → `blocked-by`). Pure regex + a node scan: no model, no tokens. Powers the web UI's live suggestions as you type. |
| `list [--type T] [--status S] [--due-before ISO] [--all] [--limit N]` | Filtered list. **Hides the done/cancelled archive by default** — pass `--all` or `--status done` to see it. |
| `due [--within-days N]` | Tasks/reminders due within N days (flags `overdue`). |
| `neighbors --id ID [--rel REL] [--depth N]` | Walk the graph out to depth N. |
| `complete --id ID [--note ...]` | Mark a task **done** — archives it (kept forever, hidden from active views), stamps `completed_at`. Never delete finished work; complete it. |
| `done [--limit N]` | The done archive — everything completed. |
| `add-skill --name slug --title T [--when triggers] [--body steps] [--with-file]` | **Learn a skill at runtime** — stores a `skill` node (triggers + full procedure in the DB, synced everywhere). **Node-first**: a local `SKILL.md` is written only with `--with-file`. |
| `ingest-skills [--dir D]... [--realm R]` | Pull existing markdown skills (plugin `skills/` + `learned_skills/`) into the DB as `skill` nodes (idempotent; re-run to refresh). |
| `match-skills "situation" [--limit N]` | Find skills matching the situation — ranked by **relevance first**, with reinforcement (usage) as a bounded booster. Read the procedure from the node body (`get-node`). |
| `use-skill --id ID` | **Reinforce** a skill after it fires (usage_count↑, confidence↑, last_used stamped). |
| `stats` | Counts by type. |
| `export --format json\|md\|both [--out DIR]` | Dump the whole graph to files. |

**Workflow rule:** before creating a node, `search` first to avoid duplicates.
If a close match exists, update/link it instead of making a new one.

## 2a. Finishing work — archive, never delete

When a task is done, **`complete` it** (don't `del-node`). It moves to the done
archive: kept forever, out of active views (`list`, `due`), still searchable and
still linked. This preserves history and lets the brain learn from what worked.

## 2b. Learned skills — neuroplasticity

The brain grows. When you notice a **repeatable procedure** — something the user
did, taught, or asked for that will recur — capture it as a learned skill so the
brain can reuse it later, in any project:

1. **Learn:** `add-skill` stores a `skill` **node in the DB** (triggers + full
   procedure in the body, synced everywhere) — no file unless you pass
   `--with-file`. Skills are nodes, not isolated markdown. Existing markdown
   skills are pulled in with `ingest-skills`.
2. **Recall:** at the start of handling a request, run `match-skills "<the
   user's situation>"` (ranked by relevance, reinforcement as a booster). If a
   skill matches, **read its procedure from the node body** (`get-node --id`) and
   follow it instead of improvising.
3. **Reinforce:** after a learned skill fires successfully, `use-skill --id` to
   strengthen it — frequently-useful skills edge up among equally-relevant matches.
4. **Connect:** link skills into the graph — `applies-to` a task/project type,
   `learned-from` the task/note that taught it, `relates-to` sibling skills — so
   learnings transfer across situations, not just the one they came from.

This is the plastic layer: the brain forms new pathways from experience and
strengthens the ones that keep paying off. Consult it before falling back to
generic behaviour.

---

## 2c. State awareness & open loops

The brain is a **state machine**: every trackable thing **holds its state until
something changes it** — nothing silently resolves. An **open loop** is anything
still awaiting action, and it stays open until closed.

- **What's a loop:** open `task`/`subtask` (status not done/cancelled), tracked
  `pr` / `email` / `message` / `reminder`, or any node carrying a non-closed
  `props.state`. Closed states: `done, cancelled, closed, answered, resolved,
  merged, shipped, deployed, declined`.
- **Move state explicitly** with `set-state --id --state <s> [--waiting-on who]`
  — it stamps `state_since` (when it changed), so "age" is honest and a loop
  that hasn't moved shows as stale. Finish tasks with `complete` (archives).
- **See it all:** `open-loops [--stale-days N]` — oldest/overdue first, grouped
  by kind + priority, with `age_days`, `overdue`, `stale`, `waiting_on`.
- **Proactively close.** The `follow-up` skill drives loops toward closed; the
  SessionStart hook surfaces them each session. Always nudge toward closing the
  oldest/highest-priority first, and flag anything the person is blocking others on.
- **Track new loops** as they appear (a PR to move, an email/message to answer)
  as `pr`/`email`/`message` nodes with a `props.state`, so they can't slip.

## 2d. Storage: neo4j master + local mirror + sections

The unified **master store is neo4j** (Gabriel's home graph; config in
`~/.second_brain/neo4j.json`). Every mutation writes to the local SQLite mirror
(durable, offline, fast reads) **and** is pushed to neo4j; if neo4j is down the
change is queued in an `outbox` and **drains automatically** on the next
successful write or an explicit `flush`. neo4j is the source of truth; SQLite is
the resilience cache.
- `neo4j-status` — configured / up / pending-ops · `flush` — drain the queue.
- `$SECOND_BRAIN_NO_AUTOSYNC=1` queues without per-op push (for heavy batch loads;
  `flush` afterwards) · `scripts/brain_sync.py` does a full bulk reconcile.
- The visualizer (`viz/serve.py`) reads from neo4j, falling back to SQLite if down.

**Sections (realms):** every node has a `realm` — the section it belongs to:
`optibus` (work; the default) and `personal` (add others as needed). Set with
`add-node --realm <section>` or `$SECOND_BRAIN_REALM`; `sections` lists the counts.

## 2e. Token-free reads (`brain_query.py`)

Everyday "what's on my plate" reads don't need the model. `scripts/brain_query.py`
answers the common date views in pure SQL — **no model call** (§0) — as a text table
(read it directly) or `--json` (for the UI/phone backend):

```
python3 scripts/brain_query.py agenda      # overdue + today + tomorrow
python3 scripts/brain_query.py open        # ALL open tasks (any/no due) — pair with --assignee
python3 scripts/brain_query.py today | tomorrow | week | closed [--since-days N]
python3 scripts/brain_query.py range --from YYYY-MM-DD --to YYYY-MM-DD [--status open|done|any]
```
The date views (today/week/…) only show **dated** tasks; use **`open`** to see
undated ones (e.g. another person's tasks) — it's the right view for the assignee filter.

Prefer it (or `graph.py search` / `open-loops`) over asking the model to recall —
recall is free. `--backend sqlite|neo4j|auto`, local-tz buckets via `--tz` /
`$SECOND_BRAIN_TZ`. The dashboard tab of `viz/serve.py` is this same layer over HTTP.

**`--mine` — only what the owner must act on.** Any view takes `--mine` (the
dashboard's "🙋 Mine · needs action" toggle → `?mine=1`) to keep ONLY the owner's
live action: **strictly `assignee == owner`** (`$SECOND_BRAIN_OWNER`, default
`Gabriel`), NOT `unassigned` and NOT someone else's, and never `delegated` or
parked `waiting`/`blocked`. Selecting the **`me`** assignee chip applies the same
strict actionable rule (not "every task tagged me").

**`--assignee me|unassigned|<name>[,…]`** — exact grouping (the dashboard renders
one toggle chip per distinct assignee, from `/api/assignees`). Distinct from
`--mine`: `assignee=me` matches only tasks *explicitly* the owner's, not
unassigned ones.

**Relation flags + expand.** Each row carries `blocked_by` / `blocks` / `related`
(derived from `blocked-by`/`depends-on`/`blocks`/`relates-to` edges) so the
dashboard flags 🔒 blocked / ⛓ blocks / 🔗 related without the model. `/api/node?id=`
returns a node's full body + named neighbors for the click-to-expand card. All
token-free.

## 2f. The web UI is a full write surface — the brain works without the model

**The model helps feed and manage the brain; it is never the only way in.** The
dashboard is a complete CRUD client: everything below is plain SQL through the
engine's `api_*` helpers, with **no model call and no tokens** — the same
guarantee as the read layer.

| Do this in the browser | Endpoint | Notes |
|---|---|---|
| Create a task | `POST /api/add-task` | `{title, body, due, assignee, priority, type, links[]}`. Type limited to `task`/`subtask`; created `open` with `props.source="web-ui"`. |
| See what a title connects to | `GET /api/suggest-links?text=` | Live as you type — see below. |
| Mark done | `POST /api/complete` | Archives (§2a): `status=done` + `completed_at`. Never deletes. |
| Move state | `POST /api/set-status` | Only `open,doing,blocked,waiting,done,cancelled`; stamps `state_since` like `set-state`. |
| Reassign | `POST /api/set-assignee` | `""` un-assigns. |
| Rename / edit notes | `POST /api/update-node` | `title` (non-empty), `body`, `priority`. |
| Set a due date | `POST /api/set-due` | The date chip ("📅 not set" when empty) opens a picker. |
| Create a relation | `POST /api/link` | Search for the other end, pick the relation, done. |
| Remove a relation | `POST /api/unlink` | The `×` on any row under **Related**. |
| Read the vocabulary | `GET /api/meta` | Relations + statuses + `writable`; the UI never hardcodes them. |
| Find a node to link | `GET /api/search?q=` | Title substring, for the relation picker. |

**Link recognition — the brain guesses the relation, you confirm it.** As you
type a task title the composer calls `suggest-links`, which scans the text for
mentions of nodes you already have (whole titles, ticket keys like `OS-84566`,
PR numbers like `#7366`, `#channel` handles, a person's name) and reads the
wording around each mention to pick the relation: *"blocked by PR #7366"* →
`blocked-by`, *"caused by …"* → `caused-by`, *"needs X first"* → `blocked-by`,
*"part of …"* → `part-of`, *"with <person>"* → `mentions`, nothing recognisable →
`relates-to`. Each suggestion renders as a row you can **re-point to any relation
or uncheck**, and your override survives further typing. It is a suggestion
engine, not an oracle — the user always has the last word.

**The UI never writes a value you did not change.** The card's assignee input,
status select and due chip all compare against the value they rendered and skip
the `POST` when it is unchanged — a change handler on a pre-filled field can fire
on a bare focus+blur, and a no-op write would churn `updated` and the neo4j sync
for nothing.

**Writes are unauthenticated.** They are bound to `127.0.0.1` by default. If you
expose the UI (`--host 0.0.0.0` for phone access), pass **`--no-write`**: every
`POST` then returns 403 and the UI hides its editing controls (`/api/meta`
reports `writable: false`).

## 3. Vocabulary

**Node types:** `person`, `task`, `subtask`, `requirement`, `decision`,
`note`, `project`, `reminder`, `writing-sample`, `association`, `meeting`,
`jira-issue`, `resource`, `skill` (learned skill), `team`, `channel`,
`process`, and the stateful trackables `pr`, `email`, `message`.

**State values** (`props.state`, or task `status`): open, doing, blocked,
waiting, needs-reply · and closed ones: done, cancelled, closed, answered,
resolved, merged, shipped, deployed, declined.

**Edge relations:** `relates-to`, `blocks`, `blocked-by`, `part-of`,
`has-subtask`, `assigned-to`, `about`, `depends-on`, `causes`, `caused-by`,
`mentions`, `authored-by`, `example-of`, `follows-up`, and for learned skills:
`applies-to`, `learned-from`, `reinforces`, `triggered-by`.

Both vocabularies are **soft** — the engine does not reject an unlisted type or
relation (hooks legitimately write `capture` nodes and `captured-from` edges).
Closed sets are enforced at untrusted boundaries instead: the web UI's write API
(`viz/serve.py`) accepts only the relations above and only `task`/`subtask`.

**Task status values:** `open`, `doing`, `blocked`, `done`, `cancelled`.
`done`/`cancelled` are the **archive** — hidden from active views but kept.

**Dates** are ISO-8601 with timezone, e.g. `2026-07-23T09:00:00+00:00`.
The person's timezone lives on their `person` node's props (`tz`). Use it when
turning "tomorrow 9am" into an ISO time.

**Props** is a free JSON dict for anything that isn't a first-class column:
`slack`, `tz`, `email`, `jira_key`, `url`, `estimate_min`, `energy`, etc.
- **`assignee`** — who owns a task (the actor). The capture hook **infers** it:
  first-person → owner; **"ask/chase/remind/ping <Name>" → the OWNER** (they do the
  asking; the person is the *target*, not the assignee); "delegate to <Name>" or
  "<Name> will/should/needs to…" → that person; otherwise left unset (NOT assumed
  the owner's). Set it explicitly when you know. Unset = unknown (still counts under
  `--mine`, shows as `unassigned` in the assignee filter).
  **Ingestion may use the model** to judge the assignee (tokens OK when capturing
  notes/emails/threads); **querying stays token-free** — the hook heuristic above
  is the no-model fallback.
- **`delegated`** — set (e.g. `ai-delegator:pr-review`) when the work is handed off;
  also excluded from `--mine`. Pair with `waiting_on`.

---

## 4. Keep docs in sync — update them on every behavior change (non-negotiable)

Docs and behavior **ship together**. Any change to how the system works updates
the docs in the *same* change — never "later". A change that alters behavior
without matching doc updates is **incomplete**.

**A "behavior change" means any of:**
- a new / changed / removed `graph.py` command or flag, or a changed default;
- a new / renamed / removed skill (`skills/*/SKILL.md`) or command (`commands/*`);
- a hook change (`hooks/`) — what fires, when, or what it stores;
- a new or changed script / endpoint (`brain_query.py`, `viz/serve.py`, sync);
- a change to storage, sync, realms, vocabulary (node types, edges, states), or
  the install/enable flow.

**Then update every doc that describes it, in the same commit:**
- `CONVENTIONS.md` (this file) — the commands table, vocabulary, and workflow
  rules. This is the source of truth every skill reads; it must never lie.
- `README.md` — the overview, skills table, engine/usage examples.
- `INSTALL.md` — if install, enable, hooks, or config changed.
- the relevant `skills/*/SKILL.md` and `commands/brain.md` — if their flow changed.

**How:** after editing the engine/hooks/scripts, `grep` the docs for the old
name/flag/behavior and fix **every** mention (don't leave a stale example). If a
default changed, update the example that shows it. Verify commands/flags shown in
docs actually exist (`graph.py <cmd> --help`). Then commit code + docs together.

**Enforced by a pre-commit hook** (`.githooks/pre-commit`): a commit that stages
behavior (`scripts/`, `hooks/`, `viz/`, or `.claude-plugin/*.json`) without also
staging a docs change (any `*.md`) is **blocked**. Enable it once per clone:

```
git config core.hooksPath .githooks
```

A genuinely doc-neutral change can bypass with `git commit --no-verify` — use it
sparingly, and only when you're sure no doc describes what changed.
