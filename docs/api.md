# HTTP API

`viz/serve.py` is a dependency-free stdlib HTTP server that serves the map +
dashboard UI and a small JSON API over the same graph. It calls **no model** — it
is the graph engine behind a socket, so everything it does is token-free.

```bash
python3 viz/serve.py                    # http://127.0.0.1:8770
python3 viz/serve.py --port 9000 --host 127.0.0.1
python3 viz/serve.py --no-write         # read-only
python3 viz/serve.py --no-open          # don't open a browser
```

## Security — read this before changing `--host`

**The write API is unauthenticated.** There is no token, no session, no CSRF
protection. Anything that can reach the port can rewrite your graph.

- Default bind is `127.0.0.1` — keep it there.
- If you must expose it, pass `--no-write`.
- Do not put it behind a plain reverse proxy and call it done; that publishes an
  unauthenticated write API to whoever can reach the proxy.

Writes are enabled unless `--no-write` is passed, in which case every `POST`
returns `{"ok": false, "error": "server started with --no-write; writes are
disabled"}`.

---

## Conventions

- All responses are JSON with an `ok` boolean; failures carry `error`.
- `POST` bodies are JSON.
- String inputs are length-capped server-side (`id` 200, `title` 300,
  `assignee` 60, `note` 500, `due` 40, `status` 20). Over-long values are
  truncated, not rejected.
- Nodes created through the UI get `props.source = "web-ui"` so UI-origin data
  is distinguishable from hook-captured and skill-written data.

---

## GET — reads

| Endpoint | Returns |
|---|---|
| `/api/graph` | nodes + edges for the map canvas |
| `/api/views` | the dashboard view payloads |
| `/api/assignees` | distinct assignees, for the filter chips |
| `/api/node?id=<id>` | one node in full plus its edges (the expand view) |
| `/api/meta` | the vocabulary — node types, edge rels, statuses |
| `/api/suggest-links` | proposed edges awaiting acceptance |
| `/api/search?q=<q>` | node picker results for the relation editor |
| `/api/<view>?<params>` | any `brain_query.py` view: `agenda`, `open`, `today`, `tomorrow`, `week`, `closed`, `range` |

`/api/meta` exists so the UI never hardcodes vocabulary — add a node type in
`graph.py` and the dashboard picks it up.

The dashboard views accept the same parameters as the CLI, as query string:

```
/api/agenda?mine=1
/api/open?assignee=unassigned
/api/range?from=2026-07-20&to=2026-07-31&status=open
/api/closed?since_days=14
/api/week?tz=America/Sao_Paulo
```

---

## POST — writes

| Endpoint | Body | Notes |
|---|---|---|
| `/api/set-due` | `{id, due}` | `due` is ISO; empty clears it |
| `/api/add-task` | `{title, type?, assignee?, priority?}` | `type` must be `task` or `subtask` |
| `/api/complete` | `{id, note?}` | moves to the archive; never deletes |
| `/api/set-status` | `{id, status, waiting_on?}` | `status` must be one of `graph.TASK_STATUSES` |
| `/api/set-assignee` | `{id, assignee}` | |
| `/api/update-node` | `{id, title?, body?, props?}` | empty `title` is rejected |
| `/api/link` | `{src, dst, rel}` | |
| `/api/unlink` | `{src, dst, rel}` | |

Example:

```bash
curl -s localhost:8770/api/set-due \
  -H 'content-type: application/json' \
  -d '{"id":"task_review-pr_1a2b","due":"2026-08-04T18:00:00-03:00"}'
```

Validation is server-side and explicit — an invalid `status` comes back with the
allowed set in the error string rather than being silently coerced.

---

## What the UI does with it

Two tabs over one dataset:

- **Map** — a hand-written force simulation (`alpha *= 0.985` per frame, so it
  needs a moment to settle). Legend chips filter by node type; clicking a node
  shows its neighbourhood. High-volume types (`channel`, `capture`) are worth
  hiding to make the rest legible.
- **Dashboard** — the date views, with a "Mine · needs action" toggle, one chip
  per assignee, and `blocked_by` / `blocks` / `related` badges from the edges.
  Cards expand on click (`/api/node`), and the date chip opens a picker that
  writes via `/api/set-due`.

Every one of those interactions is SQL. Leaving the dashboard open costs nothing.

---

## Extending it

The handler is a single `BaseHTTPRequestHandler` with a `GET` branch and a
`POST` dispatch dict. To add a write:

1. Add a method taking the parsed `data` dict, returning a JSON-able dict.
2. Register it in the `POST` dispatch table.
3. Validate and length-cap inputs with `self._str(data, key, max_len)`.
4. Go through a `graph.api_*` helper rather than raw SQL, so the neo4j outbox and
   FTS triggers stay correct.

That last point matters: writing straight to SQLite bypasses `_after_write()`,
which is what enqueues the neo4j sync. See [the neo4j backend](neo4j.md).
