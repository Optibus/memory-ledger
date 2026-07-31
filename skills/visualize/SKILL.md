---
name: visualize
description: Open the live brain UI in the browser — one local server, three sections behind a hamburger menu: a force-directed Map of every node/edge/relation (zoom/pan, search, multi-select filter dropdowns, click-to-inspect), a token-free Dashboard of the date views (agenda/open/today/tomorrow/week/closed/range) that also EDITS the brain (create tasks with auto-recognised links, complete, status/assignee/due, rename, manage relations), and Settings (timezone, default realm, database connection, prune). Use for "show me the graph", "visualize my brain/memory", "open the map/dashboard", "see the connections", "what's due", "add a task in the UI", "let me edit it myself", "change the timezone", "run prune". Auto-refreshes as the graph changes.
---

# visualize

Launch the brain UI — a dependency-free local web server serving one page with
three sections, behind the hamburger menu on the left (`m` toggles it): the
**Map** (live graph), the **Dashboard** (the `brain_query.py` date views,
token-free), and **Settings** (preferences + store upkeep). They read the same
brain file — and a configured graph mirror when one is reachable.

Load only the conventions that apply — not the whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "where the brain database lives"`
(no model call). If it returns nothing, run `ingest-conventions` once first.
CONVENTIONS §2i explains the genome.

## Launch

```
python3 "$CLAUDE_PLUGIN_ROOT/viz/serve.py" [--port 8770] [--no-open]
```

It prints a URL (default http://127.0.0.1:8770/) and opens the browser. It runs
until Ctrl-C. Because it's a long-running server, start it in the background so
you can keep working, and tell the person the URL:

```
python3 "$CLAUDE_PLUGIN_ROOT/viz/serve.py" --no-open &
```

## What the person can do in the map
- **Zoom** (scroll), **pan** (drag background), **pin** a node (drag it),
  **reset** view (double-click background).
- **Search** (top bar) — matching nodes highlight, the rest dim. The `✕` in the
  field (or Esc) clears it.
- **Filter** — four dropdowns in the top bar, each multi-select with its own
  **all** / **none**: **Node types**, **Realms**, **Kinds** (from `props.kind`),
  and **Display** ("hide done / archived", "drop unsupported"). The button shows
  what the closed menu is doing (`all`, `none`, `4/9`), and every option carries
  its live count.
- **Inspect** — click a node to see its fields and **every connection with
  direction** (← incoming, → outgoing) and the relation name; click a listed
  connection to jump to that node. This is the "where do the connections come
  from" view.
- **Live** — the page polls every ~2s, so anything a skill writes to the graph
  appears without a reload (watch the green "live" dot).

## The Dashboard section
Open the menu (`☰`, top left) → **📋 Dashboard** for the token-free date views as cards:
**agenda · open · today · tomorrow · week · closed · range** (range has date pickers +
open/done filter; closed has a since-days control). Each card shows due date
(overdue in red), type, status, realm, and `waiting on:`. This is the
`brain_query.py` layer over HTTP — no model, no tokens.

## Editing from the UI (no model needed)
The dashboard is a full editor, so the user never depends on you to maintain the
brain. Point them at it when they'd rather click than type a request:

- **＋ New task** — title, due, assignee, priority, type, notes. While they type
  the title, the server recognises nodes the text mentions and proposes the
  relation from the wording (`blocked by PR #7366` → `blocked-by`, `caused by …` →
  `caused-by`, `needs X first` → `blocked-by`, `part of …` → `part-of`, `with
  <person>` → `mentions`). Each suggestion can be re-pointed to any relation or
  unchecked before saving.
- **Expand a card** → ✓ Complete (archives, never deletes), status dropdown,
  assignee field, ✎ Edit (title/body), a `×` on any relation to remove it, and a
  search box to add a new one.
- Same engine you call: `graph.py suggest-links "<text>"` returns the identical
  suggestions, so you and the UI never disagree.

If the server was started with `--no-write` the editing controls are hidden and
every write returns 403 — tell the user to restart without that flag.

## The Settings section
Pinned at the bottom of the menu (**⚙ Settings**). Point the person here instead
of editing files or exporting env vars:

- **Time zone** — which calendar day "today", "tomorrow" and "overdue" mean.
  Saved to `~/.second_brain/settings.json`, so `brain_query.py`, the hooks and any
  cron bucket dates the same way. `$SECOND_BRAIN_TZ` still wins, and the page says
  so when it is set.
- **Defaults & interface** — default realm for new nodes, map poll and dashboard
  refresh intervals.
- **Database connection** — brain file, node/edge counts, which store is being
  read, queued writes; plus the neo4j master's url/db/user/password with **Test**
  and **Flush queued**. An empty url goes back to sqlite-only. The password is
  never sent back to the browser.
- **Prune** — runs `maintain`: old captures fold into one digest per day so reads
  stay small. Non-destructive; nothing is deleted (CONVENTIONS §2a).

## Notes
- The server binds to 127.0.0.1 only by default. Use `--host 0.0.0.0` (behind a
  tunnel) to reach it from your phone on the LAN — and pass `--no-write` when you
  do, because the write API is unauthenticated.
- Colours are per node type (see the Node types dropdown); node size grows with
  connection count.
- If the port is busy, pass a different `--port`.
