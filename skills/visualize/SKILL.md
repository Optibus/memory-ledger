---
name: visualize
description: Open the live brain UI in the browser — one local server, three sections behind a hamburger menu: a force-directed Map of every node/edge/relation (zoom/pan, search, multi-select filter dropdowns, click-to-inspect), a token-free Dashboard of the date views (agenda/open/today/tomorrow/week/closed/range) that also EDITS the brain (create tasks with auto-recognised links, complete, status/assignee/due, rename, manage relations), and Settings (timezone, default realm, database connection, prune). Use for "show me the graph", "visualize my brain/memory", "open the map/dashboard", "see the connections", "what's due", "add a task in the UI", "let me edit it myself", "change the timezone", "run prune". Auto-refreshes as the graph changes.
---

# visualize

Launch the brain UI, and hand the person a surface where they do not need you.
That second half is the point: the dashboard is a full write surface, so the brain
keeps working when the model is not in the room.

## When to use

"Show me the graph", "open the dashboard", "see the connections", "what is due",
"let me edit it myself", "change the timezone", "run prune".

## Given

- **The server**, which is dependency-free and local:

  ```
  python3 "$CLAUDE_PLUGIN_ROOT/viz/serve.py" [--port 8770] [--no-open]
  ```

  It prints a URL, defaulting to `http://127.0.0.1:8770/`, and runs until Ctrl-C.
  Because it is long-running, start it in the background and give the person the
  URL. It binds to 127.0.0.1 only unless `--host 0.0.0.0` is passed.
- **Three sections behind the hamburger menu** (`m` toggles it), reading the same
  brain file and a configured graph mirror when one is reachable:
  - **Map.** Zoom, pan, pin a node by dragging, reset with a double-click. Search
    highlights matches and dims the rest. Four multi-select filters with live
    counts: node types, realms, kinds from `props.kind`, and display toggles.
    Clicking a node shows its fields and every connection with direction, and
    clicking a connection jumps to that node. The page polls every two seconds.
  - **Dashboard.** The `brain_query.py` date views as cards: agenda, open, today,
    tomorrow, week, closed, range. No model, no tokens.
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
- **Reads:** every node type in the graph, through `viz/serve.py`.
- **Writes:** none by this skill. The person writes, through the UI.

## Deliverables

- **The server is running and the person has the URL**, started in the background
  so the session is not blocked.
- **They know which section answers their question.** Map for connections,
  Dashboard for dates and edits, Settings for preferences and upkeep.
- **They know they can edit without you.** When the request was about maintaining
  the brain rather than understanding it, the dashboard's write surface is named,
  because depending on the model for upkeep is the failure this section prevents.
- **A read-only server is diagnosed, not worked around.** If it was started with
  `--no-write`, the editing controls are hidden and writes return 403. Say so and
  say to restart without the flag.

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

## Reply (focus contract)

The URL and the one section that answers what they asked.

> Brain UI is up: **http://127.0.0.1:8770/**. Open the menu and pick Dashboard for
> what is due. You can add and complete tasks there without me.
