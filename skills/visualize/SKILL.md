---
name: visualize
description: Open the live brain UI in the browser — one local server, two tabs: a force-directed Map of every node/edge/relation (zoom/pan, search, type filters, click-to-inspect) and a token-free Dashboard of the date views (agenda/open/today/tomorrow/week/closed/range). Use for "show me the graph", "visualize my brain/memory", "open the map/dashboard", "see the connections", "what's due". Auto-refreshes as the graph changes.
---

# visualize

Launch the brain UI — a dependency-free local web server serving one page with
two tabs: the **Map** (live graph) and the **Dashboard** (the `brain_query.py`
date views, token-free). Both read the same store (neo4j when reachable, else the
local SQLite mirror).

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first (for the DB
location; the server reads the same brain).

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
- **Search** (top bar) — matching nodes highlight, the rest dim.
- **Filter by type** — click chips in the legend to toggle node types; tick
  "hide done / archived" to drop completed work.
- **Inspect** — click a node to see its fields and **every connection with
  direction** (← incoming, → outgoing) and the relation name; click a listed
  connection to jump to that node. This is the "where do the connections come
  from" view.
- **Live** — the page polls every ~2s, so anything a skill writes to the graph
  appears without a reload (watch the green "live" dot).

## The Dashboard tab
Switch to **📋 Dashboard** (top toggle) for the token-free date views as cards:
**agenda · open · today · tomorrow · week · closed · range** (range has date pickers +
open/done filter; closed has a since-days control). Each card shows due date
(overdue in red), type, status, realm, and `waiting on:`. This is the
`brain_query.py` layer over HTTP — no model, no tokens.

## Notes
- The server binds to 127.0.0.1 only by default. Use `--host 0.0.0.0` (behind a
  tunnel) to reach it from your phone on the LAN.
- Colours are per node type (see the legend); node size grows with connection count.
- If the port is busy, pass a different `--port`.
