---
name: visualize
description: Open the live brain UI in the browser — one local server, four sections behind a hamburger menu: a force-directed Map of every node/edge/relation (zoom/pan, search, multi-select filter dropdowns, click-to-inspect), a token-free Dashboard of the date views (agenda/open/today/tomorrow/week/closed/range) that also EDITS the brain (create tasks with auto-recognised links, complete, status/assignee/due, rename, manage relations), Skills (the two contract trees and the rule catalogue, where new skills and rules are AUTHORED and where markdown export/ingest moves a brain between machines), and Settings (timezone, default realm, database connection, prune). Use for "show me the graph", "visualize my brain/memory", "open the map/dashboard", "see the connections", "what's due", "add a task in the UI", "create a skill/rule in the UI", "sync my skills to another machine", "let me edit it myself", "change the timezone", "run prune". Auto-refreshes as the graph changes.
---

# visualize

Launch the brain UI, and hand the person a surface where they do not need you.
That second half is the point: the dashboard is a full write surface, so the brain
keeps working when the model is not in the room.

## When to use

"Show me the graph", "open the dashboard", "see the connections", "what is due",
"let me edit it myself", "write a skill myself", "add a rule in the UI", "move my
skills to my other machine", "change the timezone", "run prune".

## Given

- **The server**, which is dependency-free and local:

  ```
  python3 "$CLAUDE_PLUGIN_ROOT/scripts/serve.py" [--port 8770] [--no-open]
  ```

  It prints a URL, defaulting to `http://127.0.0.1:8770/`, and runs until Ctrl-C.
  **Nothing to install by hand.** The UI is a React app and the server builds it
  on the first run, which needs `npm` on the machine. Without it the API still
  answers in full and only the page is missing, saying why.
  Because it is long-running, start it in the background and give the person the
  URL. It binds to 127.0.0.1 only unless `--host 0.0.0.0` is passed.
- **Four sections behind the hamburger menu** (`m` toggles it), each with its own
  URL (`/map`, `/dashboard`, `/skills`, `/settings`) so a section can be linked
  to directly, reading the same brain file and a configured graph mirror when one
  is reachable:
  - **Map.** Zoom, pan, pin a node by dragging, reset with a double-click. Search
    highlights matches and dims the rest. Four multi-select filters with live
    counts: node types, realms, kinds from `props.kind`, and display toggles.
    Clicking a node shows its fields and every connection with direction, and
    clicking a connection jumps to that node. The page polls every two seconds.
  - **Dashboard.** The `brain_query.py` date views as cards: agenda, open, today,
    tomorrow, week, closed, range. No model, no tokens.
  - **Skills.** The two skill trees from the graph (§5a), built-in and user,
    nested by `requires`, **and the full rule catalogue beside them**. Opening a
    skill shows what node types it reads and writes, what it waits for or is
    enriched by, and the rules it answers to, split into declared, matched and
    always-on, plus its relations one level deep. Clicking a rule reads it in
    full, from either place. One filter narrows both: free text over names,
    triggers, `scenario` and node types, plus source (built-in / user / learned)
    and domain. The `+` button **authors** a new skill or rule, refusing an
    incomplete contract at write time; rules, skills and node types are chosen
    from searchable cards rather than dropdowns, so the full contract is one
    click away while choosing. An existing contract is still edited where its
    check lives, in `SKILL.md` or with `add-skill`. Two buttons export the
    learned tree to markdown and read a folder back, which is how a brain moves
    between machines with no master to mirror through.
  - **Settings.** Timezone, default realm, map and dashboard refresh intervals,
    the database connection including the neo4j master with Test and Flush
    queued, and Prune.
- **The write path.** New task with title, due, assignee, priority, type and
  notes, where the server proposes relations from the wording as they type
  (`blocked by PR #7366` becomes `blocked-by`, `part of ...` becomes `part-of`,
  `with <person>` becomes `mentions`), each re-pointable or removable before
  saving. Expanding a card gives complete, status, assignee, edit, and relation
  add and remove. The same engine you call: `graph.py suggest-links "<text>"`
  returns identical suggestions, so you and the UI never disagree.

## Rules honored

Ask the genome for the rules that apply before launching (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "where the brain database lives"
```

Accountable for: `rule_s2g_the-web-ui-is-a-full-write-surface-the-b`,
`rule_s2f_token-free-reads-brain-query-py`,
`rule_s2a_finishing-work-archive-never-delete` (prune is non-destructive).

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Reads:** every node type in the graph, through `scripts/serve.py`.
- **Writes:** none by this skill. The person writes, through the UI.

## Deliverables

- **The server is running and the person has the URL**, started in the background
  so the session is not blocked.
- **They know which section answers their question.** Map for connections,
  Dashboard for dates and edits, Skills for what a skill is accountable for,
  which rules govern it, what the genome holds, and where a new contract is
  written or moved between machines, Settings for preferences and upkeep.
- **They know they can edit without you.** When the request was about maintaining
  the brain rather than understanding it, the dashboard's write surface is named,
  because depending on the model for upkeep is the failure this section prevents.
- **A read-only server is diagnosed, not worked around.** If it was started with
  `--no-write`, the editing controls are hidden and writes return 403. Say so and
  say to restart without the flag.
- **A missing page is diagnosed as a missing toolchain, not a broken server.**
  The first start compiles the UI. If `npm` is absent the page returns 503 while
  every endpoint keeps answering, so the fix is to install Node.js, never to
  restart.
- **A request about the API itself is answered with `/api/docs`**, the Swagger UI
  the server builds from its own routes. Describing the endpoints from memory is
  how a stale answer gets into someone's client.

## Judged against

- **The URL in the reply loads the page.**
- **The person was pointed at the section that answers what they asked**, not at
  the UI in general.
- **Nothing in the graph changed because of this skill.**

## Guardrails — must not

- **Must not expose the write API on a non-local host.** `--host 0.0.0.0` reaches
  the LAN and the write API is unauthenticated, so it goes together with
  `--no-write`.
- **Must not run the server in the foreground.** It runs until Ctrl-C and would
  block the session.
- **Must not describe prune as deleting.** It folds old captures into one digest
  per day, and deletes nothing.
- **Must not present a stale Skills tree as the truth.** The section draws the
  edges `sync-skill-tree` wrote. If a declaration changed since the last sync, say
  to re-run it rather than reading the page as current.
- **Must not offer the `+` button as a way to edit an existing contract.** It
  authors new ones. A built-in skill's contract is gated by the pre-commit
  checker against its `SKILL.md`, and pointing someone at the page to change one
  routes them around the only check it has.

## Reply (focus contract)

The URL and the one section that answers what they asked.

> Brain UI is up: **http://127.0.0.1:8770/**. Open the menu and pick Dashboard for
> what is due. You can add and complete tasks there without me.
