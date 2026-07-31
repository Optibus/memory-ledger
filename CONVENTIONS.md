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

| Hook | Injected into context | Latency |
|---|---|---|
| `UserPromptSubmit` | **208 bytes** (~52 tokens), on every prompt | 64 ms |
| `SessionStart` | **~1.0 KB** (~267 tokens; open-loops brief, capped at 6 loops so it does not grow with the graph) | 185 ms |
| `Stop` | **nothing** (writes only) | 57 ms |

A heavy day — ~60 prompts across ~6 sessions — is ~4,700 injected tokens, a few
cents, **under 0.05% of what a working day of Claude Code costs**. It likely pays
for itself, since the injected line tells the model not to re-store the turn and to
recall through the free paths instead. So: effectively free, **not literally zero**.
Say "no model call" when you mean it. Re-measure with the recipe in
[`docs/performance.md`](docs/performance.md).

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
  neo4j.json          optional graph-mirror config (secret — never commit)
  (routines, definitions live here too)
```

Path resolution: `$SECOND_BRAIN_DB` (explicit file) → `$SECOND_BRAIN_HOME/graph.db`
→ `~/.second_brain/graph.db`. Copying `graph.db` moves the whole brain; copying
the home dir moves brain + learned skills. This works identically in Claude Code
and the Claude app.
Every command prints JSON to stdout, and **wraps its result in an envelope** —
`{"ok": true, ...}` on success, `{"ok": false, "error": "..."}` on failure. The
payload key varies by command: a write returns the whole node under **`node`**
(so a created id is `.node.id`, **not** `.id`), reads return **`results`** +
`count`, `del-node` returns `deleted`. Bad flags are an argparse error on
**stderr** with a non-zero exit — so parse stdout as JSON *and* let stderr
through, or a typo'd flag looks like an empty result:

```bash
ID=$(python3 scripts/graph.py add-node --type task --title "..." | python3 -c \
  'import sys,json; print(json.load(sys.stdin)["node"]["id"])')
```

Commands:

| Command | Purpose |
|---|---|
| `init` | Create the DB (idempotent). |
| `add-node --type T --title "..." [--body ...] [--props JSON] [--status S] [--due ISO] [--id ID] [--realm R] [--area A]` | Create a node. `--area` files it under a domain area in the same call (§2e) and **errors instead of creating an orphan** if the area does not exist. Returns the full node under `node` — read the new id from **`.node.id`**. There is **no `add-task` subcommand**: a task is `add-node --type task --status open` (the `add-task` *skill* wraps that and adds micro-steps; the web UI's is `POST /api/add-task`). |
| `update-node --id ID [--title][--body][--props JSON][--status][--due]` | Update fields. `--props` MERGES into existing props. |
| `get-node --id ID` | One node **plus all its edges** (neighbors named). |
| `del-node --id ID` | Delete a node (its edges cascade). |
| `add-edge --src ID --dst ID --rel REL [--props JSON]` | Link two nodes (idempotent). |
| `del-edge --src ID --dst ID --rel REL` | Remove a link. |
| `search "text" [--type T] [--limit N] [--budget CHARS] [--full]` | Full-text search over title+body (prefix-matched). **Budgeted by default** — bodies are trimmed to share `--budget` (2,600) and only decision-relevant props are returned; uncapped it was ~9k chars where `recall` answered the same cue in ~1.5k. `--full` restores whole bodies; `get-node --id` reads one completely. |
| `suggest-links "text" [--limit N] [--exclude ID]` | **Link recognition** — which known nodes this text mentions, and the relation its wording implies (`blocked by X` → `blocked-by`). Pure regex + a node scan: no model, no tokens. Powers the web UI's live suggestions as you type. |
| `recall "cue" [--limit N] [--depth 2] [--budget CHARS]` | **Associative recall — prefer this over `search`.** Seeds on full-text, spreads activation across edges, modulates by memory strength, returns a SLIM ranked payload inside a char budget. One call replaces `search` + `neighbors` and costs ~40% fewer chars. |
| `consolidate [--day D] [--older-than N]` | Roll a day's `capture` nodes into one digest note. **Non-destructive** — every capture is kept and linked; the digest is what recall surfaces. |
| `list [--type T] [--status S] [--area A] [--due-before ISO] [--all] [--limit N]` | Filtered list. **Hides the done/cancelled archive by default** — pass `--all` or `--status done` to see it. `--area` keeps only what is `part-of` that domain area (§2e). |
| `areas` | Every domain area with its member count (§2e). Token-free. |
| `add-area --title T [--id ID] [--domain KEY] [--body ...] [--realm R]` | Create a domain area. Idempotent on `--id`; default id is `hub_<slug of title>`. |
| `match-rules "situation" [--limit N] [--budget CHARS] [--full]` | **Which conventions apply to THIS situation** (§2i) — the token-light replacement for reading this whole file (~8% of it). No model call. Always-apply rules are appended, not counted against `--limit`. |
| `ingest-conventions [--file F] [--realm R]` | Load this file into the brain as `rule` nodes, one per section (§2i). Idempotent and **never overwrites a user-authored or user-edited rule**. |
| `add-rule --title T [--body ...] [--scenario CUES] [--section S] [--source user\|learned\|plugin] [--overrides ID] [--non-negotiable] [--id ID] [--realm R]` | Author or override a rule — the mutable half of the genome (§2i). `--overrides` marks the shipped rule `superseded`. **`--scenario` is indexed** — put the words a person would actually type there, including ones the body never uses; that is what `match-rules` fires on. Same for a skill's `triggers`/`description`. |
| `park [--body TEXT] [--title T] [--about ID] [--area A]` | **Move context OUT of the window** (§2j): stores the blob (stdin or `--body`) and returns a tiny receipt, so the model can drop the content. |
| `unpark --id ID [--offset N] [--chars N]` | Read parked context back, whole or sliced. |
| `resume [--budget CHARS] [--days N] [--limit N]` | **Rehydrate after `/clear`** (§2j) — the thread that was in play, what's parked, what's open, inside a budget. No model call. |
| `maintain [--keep-days N]` | Brain-side upkeep: consolidate old captures into per-day digests, repair reversed digest links, report the read reduction. Non-destructive, idempotent, schedulable. |
| `genome [--refresh] [--out DIR]` | The brain's own rebuild blueprint (§2i). `--refresh` re-derives the API surface + DB schema from the code; `--out` writes the full kit for an AI with no plugin. |
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

## 2d. Storage: one local file, an optional mirror, and sections

**The brain is one local SQLite file, and that is the whole product.** It is
durable, offline, fast to read, and portable — copy `graph.db` and the brain moves
with it. Nothing about the brain requires a network.

**Optional graph mirror.** If a `graph` backend is configured, every mutation also
pushes there, so several machines can share one brain. Configure it with
`~/.second_brain/neo4j.json` or the `SECOND_BRAIN_NEO4J_*` env vars — any
reachable graph server works; the plugin ships no default and connects to nothing
unless you configure it. When the mirror is unreachable the change is queued in an
`outbox` and **drains automatically** on the next successful write or an explicit
`flush`, so losing the mirror never blocks or loses a write.
- `neo4j-status` — configured / reachable / pending-ops · `flush` — drain the queue.
- `$SECOND_BRAIN_NO_AUTOSYNC=1` queues without per-op push (for heavy batch loads;
  `flush` afterwards) · `scripts/brain_sync.py` does a full bulk reconcile.
- The visualizer (`viz/serve.py`) reads the mirror when one is reachable, else the
  local file.

Node `type` is a property in the mirror, not a label, so changing a node's type
needs no label surgery — re-emitting the node through the API is enough.

**Sections (realms):** every node has a `realm` — the section it belongs to:
`optibus` (work; the default), `shared`, and `personal` (add others as needed).
Set with `add-node --realm <section>` or `$SECOND_BRAIN_REALM`; `sections` lists
the counts.

## 2e. Areas — one hub per domain

**Nothing durable floats loose.** A quote, a health note, a way of working: each
belongs to a **domain area** so it can be *queried as a domain* rather than found
by guessing full-text. An area is a node of type `area`, content points at it with
`part-of`, and the id convention is `hub_<domain>`:

```
python3 scripts/graph.py areas                       # every domain + member count
python3 scripts/graph.py list --area quote           # everything in one domain
python3 scripts/graph.py add-node --type quote --area hub_quote --title "..."
python3 scripts/graph.py add-area --title "Finance — money, contracts" --domain finance
```

`--area` accepts the full id (`hub_quote`) **or the bare domain** (`quote`). It
resolves the area *before* creating the node, so a typo returns an error listing
the known areas rather than leaving a node outside every domain.

| Area | Domain | Realm | Holds |
|---|---|---|---|
| `hub_process` | process | optibus | Gabriel's ways of working — his `decision`s and discretionary methods |
| `hub_quote` | quote | shared | Quotes he keeps, with `attributed_to` / `source_work` in props |
| `hub_health` | health | personal | Symptoms, episodes, energy, appointments — anything affecting capacity |

**Realm and area are orthogonal.** The realm is *which section of life* (work /
personal / shared); the area is *which domain of meaning*. A health note is realm
`personal`, area `health`. Add a new area the moment a second item of a domain
shows up — two loose notes of a kind is the signal, not a full taxonomy up front.

Areas are also where **cross-domain links earn their keep**: link a quote to the
`decision` it justifies, or a health episode to the day it cost, so recall can
explain *why*, not just list *what*.

## 2f. Token-free reads (`brain_query.py`)

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

**`--realm <r>[,…]`** — scope any view to one or more sections of life
(`?realm=optibus,shared` on the dashboard). Omit it for every realm. A node with
no realm counts as `$SECOND_BRAIN_REALM` (default `optibus`), the same default the
engine writes with, so filtering never silently drops untagged nodes. **Realms are
open vocabulary** — never hardcode the list. Read it from `/api/realms` (which also
returns the server default) or `brain_query.realms()`; `graph.py sections` is the
CLI equivalent. Same for node types: `/api/meta` returns `realms` and `types` from
the brain precisely so nothing downstream ships a fixed list.

**Relation flags + expand.** Each row carries `blocked_by` / `blocks` / `related`
(derived from `blocked-by`/`depends-on`/`blocks`/`relates-to` edges) so the
dashboard flags 🔒 blocked / ⛓ blocks / 🔗 related without the model. `/api/node?id=`
returns a node's full body + named neighbors for the click-to-expand card. All
token-free.

## 2g. The web UI is a full write surface — the brain works without the model

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
on a bare focus+blur, and a no-op write would churn `updated` and the mirror sync
for nothing.

**Writes are unauthenticated.** They are bound to `127.0.0.1` by default. If you
expose the UI (`--host 0.0.0.0` for phone access), pass **`--no-write`**: every
`POST` then returns 403 and the UI hides its editing controls (`/api/meta`
reports `writable: false`).

## 2h. Memory dynamics — how recall behaves like memory

Three properties, all pure SQL/regex (recall stays model-free):

**Strength + decay.** Every node carries `access_count` / `last_accessed` /
`strength` (0–1). **Reading reinforces** — `recall` and `match-skills` bump the
nodes they return. Strength decays with disuse on a ~21-day half-life, so a note
used weekly outranks one written once and never reopened. Nothing is ever deleted;
a faded memory just stops winning the ranking. A node accessed 3× reads 0.56 fresh,
0.28 after three untouched weeks, 0.00 after six months.

**Spreading activation.** `recall` seeds on FTS, then spreads across edges to
`--depth` (default 2), summing convergent paths so independent evidence adds up.
Two dampers keep it honest:
- **fan-out normalisation** — a node divides activation among its neighbours
  instead of giving each a full share. Without it the owner's `person` node (~80
  edges) collects activation from every seed and re-broadcasts it, and a query for
  a ticket comes back as a list of people.
- **ubiquity penalty** — an *associated* hit is divided by `log2(1+degree)`. A node
  wired to everything answers every query and distinguishes none. Lexical matches
  are exempt; they earned their rank.
Cue words are stopword-filtered (English + pt-BR), since function words widen the
sweep for nothing.

**Daily consolidation runs itself.** The `SessionStart` hook fires
`consolidate --older-than 1` **once per day, detached** — closed days get folded
into digests while today's captures stay raw until the day ends. It lives in the
hook, not in a cloud routine, because the brain is a LOCAL file that a cloud
routine cannot reach; session start is the one moment we know we are on the machine
that owns the graph. Guarded by a `~/.second_brain/.last_consolidate` stamp, never
blocks the session, and is safe to interrupt (non-destructive, so the next session
just picks it up). Opt out with `$SECOND_BRAIN_NO_CONSOLIDATE`.

**Hebbian wiring.** Co-activated nodes get a weighted `relates-to` edge — but only
after the pair fires together **3 times** (tallied in the local `coactivation`
table, which never syncs). One co-occurrence is a coincidence, not a pattern: an
early version wired a shapefile ticket to the `export-memory` skill because both
matched "export". Pairs already joined by any relation are skipped, and
`skill`/`channel`/`person`/`capture` never auto-wire — those are hubs, or they get
linked by intent (§2b).

**Why this is also a token optimisation.** Better ranking means fewer results are
needed, and payloads carry a snippet instead of a whole body. Measured on the real
graph: `match-skills` **7,449 → 682 chars (−91%)** — and §3 mandates it on every
routed request — while a context lookup drops **41%**. About **2,000 tokens saved
per routed request**. `match-skills` returns cue-sized rows now; read the full
procedure with `get-node --id` for the one skill you actually pick (`--full`
restores the old payload).

## 2i. The genome — the brain carries its own Claude layer

**This document is the base layer, not the source of truth.** The plugin ships it;
the brain stores it, the user mutates it, and every read goes through the graph. So
a brain is self-describing: given only `graph.db`, any AI can rebuild the
interaction layer and reconnect to the storage.

Two areas hold it (§2e):

| Area | Node type | Holds |
|---|---|---|
| `hub_convention` | `rule` | the operating rules — this file, one node per section, plus the user's own |
| `hub_genome` | `spec` | the rebuild blueprint: storage, hooks, API surface, DB schema, sync, restore path |

**Read rules by scenario, never the whole file.** This is the point:

```
python3 scripts/graph.py match-rules "<what you are about to do>"
```

No model call. It returns the sections that match the cue, plus the always-apply
rules, trimmed to a `--budget` (default 2,600 chars). Measured on the real graph:
**~2.4k chars against 28.5k for the full document — about 8%**, and the rule you
actually need comes back first. `--full` returns whole bodies; `get-node --id` reads
one rule completely.

**Precedence — the user redefines the brain.** `user` (100) > `learned` (50) >
`plugin` (0). Precedence is a *bounded booster on relevance*, never a re-sort, so a
user rule edges past an equally-relevant default but a barely-relevant one cannot
outrank a real match. `add-rule --overrides <plugin rule id>` marks the shipped rule
`superseded` — kept as history (§2a), excluded from matching.

**`ingest-conventions` is non-destructive.** Re-run it any time to refresh the base
layer: it updates `source=plugin` rules and **keeps every user-authored or
user-edited one**. Restoring the plugin can therefore never undo the user's brain.
Only sections listed in `ALWAYS_APPLY_SECTIONS` (§0, §1) are forced into every
answer — keep that set tiny, since each is a fixed cost on every lookup. Note the
doc labels §4 non-negotiable too, but it binds only when you change behaviour, so it
is matched, not forced.

**`genome --refresh` re-derives what can be derived.** `spec_api_surface` is
introspected from the engine's own argparse and `spec_db_schema` from `sqlite_master`,
so neither can drift from the code the way a hand-written list would. `genome --out
DIR` writes the whole kit — specs, rules, and a manifest — for an AI with no plugin.
Start at `spec_rebuild`.

## 2j. Clearing context is cheap — the brain is what makes it safe

**The context window is the expensive thing, not the graph.** Every request pays
for the whole accumulated window; the graph costs only what you read from it. So
the way to reduce spend is not to store less — it is to **carry less**, and query
instead.

That inverts the usual instinct. Do not preserve context "just in case": park it,
clear it, and ask for it back if it turns out to matter.

**Three moves, in the order you reach for them.**

1. **`park`** — something bulky is in the conversation (a transcript, a long file,
   an analysis) and you are done reading it. Park it and drop it:
   ```
   <blob> | python3 scripts/graph.py park --title "..." --about <node-id>
   ```
   It returns a receipt of a couple of hundred chars — id, size, first line — and
   nothing else. Measured: 2,893 chars in, 245 chars of receipt out. Read it back
   whole or sliced with `unpark --id`.
2. **`/clear`** — then actually clear. This is the single biggest saving available
   and it is under-used because it feels lossy. It is not lossy here: the hooks
   already captured the turns, and the semantic nodes were written as you went.
3. **`resume`** — rehydrate the thread: what was in play, what is parked, what is
   still open, inside a `--budget` (default 1,800 chars). Pure SQL and full-text,
   **no model call**. `SessionStart` fires on `/clear`, so the brief hands most of
   this back automatically — `resume` is the fuller, on-demand version.

**Answer from a query, not from memory of the window.** When a question is about
something already in the brain, look it up (`recall`, `search`, `brain_query.py`,
`match-rules`) rather than keeping the answer resident in context so it can be
re-read later. A budgeted lookup costs ~1.5k chars; the same material sitting in
the window costs that on *every* subsequent request.

**Read paths are budgeted for this reason.** `search`, `recall`, `match-rules`,
`match-skills` and `resume` all cap their payload and point at `get-node --id` for
the one thing you actually want in full. `search --full` exists but is the
exception, not the habit.

**`maintain` is brain-side upkeep, and is a different thing.** It consolidates old
`capture` nodes into one digest per day so *reads* stay small as the store grows —
it does not clear context and it never deletes (§2a). Currently 141 captures
holding 32,869 chars sit behind 3,505 chars of digest, an **89% reduction in what a
read has to traverse**. Safe to schedule; the `SessionStart` hook already runs the
daily pass.

## 3. Vocabulary

**Node types:** `person`, `task`, `subtask`, `requirement`, `decision`,
`note`, `project`, `reminder`, `writing-sample`, `association`, `meeting`,
`jira-issue`, `resource`, `skill` (learned skill), `team`, `channel`,
`area` (domain hub), `quote`, `parked` (context moved out of the window, §2j),
`rule` (a convention, §2i), `spec` (a genome
blueprint, §2i), `process`, and the stateful trackables `pr`, `email`, `message`.

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
- the relevant `skills/*/SKILL.md` and `commands/ledger.md` — if their flow changed.

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
