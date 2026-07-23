---
name: visualize
description: Open a live, interactive map of the brain in the browser — a force-directed graph of every node, edge, and relation, with zoom/pan, search, type filters, and click-to-inspect showing where each connection comes from. Use for "show me the graph", "visualize my brain/memory", "open the map", "see the connections". Auto-refreshes as the graph changes.
---

# visualize

Launch the graph visualizer — a dependency-free local web server that renders
the live brain.

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

## Notes
- The server binds to 127.0.0.1 only (local). No data leaves the machine.
- Colours are per node type (see the legend); node size grows with connection count.
- If the port is busy, pass a different `--port`.
