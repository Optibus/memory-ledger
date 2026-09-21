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
| `/api/realms` | every realm in the brain + `{nodes, open_tasks}` counts, and the server's `default` |
| `/api/kinds` | every `props.kind` + the same counts, and the `none_token` for nodes carrying no kind |
| `/api/node?id=<id>` | one node in full plus its edges (the expand view) |
| `/api/meta` | the vocabulary — node types, realms, kinds, edge rels, statuses |
| `/api/suggest-links` | proposed edges awaiting acceptance |
| `/api/search?q=<q>` | node picker results for the relation editor |
| `/api/settings` | stored preferences, plus what is actually `effective` and which keys the environment has overridden |
| `/api/timezones` | every IANA zone name, for the timezone picker |
| `/api/status` | where the data lives: db path, home, node/edge counts, queued writes, loose captures, and the neo4j master's reachability |
| `/api/<view>?<params>` | any `brain_query.py` view: `agenda`, `open`, `today`, `tomorrow`, `week`, `closed`, `range` |

`/api/meta` exists so the UI never hardcodes vocabulary. Two of those sets are
**closed** — `rels` and `statuses` are enforced on write — and two are **open**:
`types`, `realms` and `kinds` are whatever the brain actually contains. Nobody's
brain has the same list, so all three are read at runtime and the UI renders what
comes back. Invent a realm with `add-node --realm skunkworks`, or a kind with
`--props '{"kind":"postmortem"}'`, and it is a filter chip on the next poll, with
no code change.

`/api/realms` reports two counts per realm because they answer different
questions: `nodes` is what the map filters over, `open_tasks` is what the
dashboard filters over. A realm with 40 nodes and 0 open tasks is real, and the
chip says so rather than showing a number that filters down to nothing.

Nodes in `/api/graph` each carry a `realm`, and `meta.by_realm` gives the totals —
that is what drives the map's realm legend.

The dashboard views accept the same parameters as the CLI, as query string:

```
/api/agenda?mine=1
/api/open?assignee=unassigned
/api/agenda?realm=personal
/api/open?realm=optibus,shared&mine=1
/api/open?kind=postmortem
/api/agenda?kind=(none)          # nodes that carry no kind at all
/api/range?from=2026-07-20&to=2026-07-31&status=open
/api/closed?since_days=14
/api/week?tz=America/Sao_Paulo
```

`realm` takes a comma list; omitting it means every realm. An empty realm on a
node is normalised to the configured default (`$SECOND_BRAIN_REALM`, `optibus`
out of the box), so filtering never silently drops untagged nodes.

`kind` works the same way over `props.kind`, which is optional and sparse — most
nodes have none. Rather than hide those, they land in an explicit `(none)` bucket,
so "everything without a kind" is a filter you can express. On the **map** every
kind is offered; on the **dashboard** only kinds that appear on open tasks are,
since the dashboard lists tasks and a kind with none would be dead UI.

---

## POST — writes

| Endpoint | Body | Notes |
|---|---|---|
| `/api/set-due` | `{id, due}` | `due` is ISO; empty clears it |
| `/api/add-task` | `{title, type?, assignee?, priority?, realm?, due?, body?, links?}` | `type` must be `task` or `subtask`; `realm` defaults to the server's |
| `/api/complete` | `{id, note?}` | moves to the archive; never deletes |
| `/api/set-status` | `{id, status, waiting_on?}` | `status` must be one of `graph.TASK_STATUSES` |
| `/api/set-assignee` | `{id, assignee}` | |
| `/api/update-node` | `{id, title?, body?, props?}` | empty `title` is rejected |
| `/api/link` | `{src, dst, rel}` | |
| `/api/unlink` | `{src, dst, rel}` | |
| `/api/settings` | `{tz?, realm?, map_poll_seconds?, dash_refresh_seconds?}` | writes `<home>/settings.json`; only these keys, each validated. `""` **removes** a key (back to env/system default) |
| `/api/neo4j-config` | `{url, db?, user?, password?}` | writes `<home>/neo4j.json` (mode 600) and reports whether the master answered. Blank `password` keeps the stored one; empty `url` deletes the file (sqlite-only) |
| `/api/neo4j-test` | `{}` | runs `RETURN 1` against the configured master |
| `/api/flush` | `{}` | drains the outbox to the master |
| `/api/prune` | `{keep_days?}` | runs `graph.py maintain` (default `2`). Non-destructive — consolidates, never deletes |

Example:

```bash
curl -s localhost:8770/api/set-due \
  -H 'content-type: application/json' \
  -d '{"id":"task_review-pr_1a2b","due":"2026-08-04T18:00:00-03:00"}'
```

Validation is server-side and explicit — an invalid `status` comes back with the
allowed set in the error string rather than being silently coerced.

**A disconnected client is not a 500.** The map polls, so a reload aborts a
response mid-write. The server returns silently rather than reporting the error
back down a socket that has already closed, which would raise a second exception
out of the error path. A genuine handler bug still comes back as a 500 with its
reason, and the server stays up either way.

---

## Settings

Preferences live in **`<home>/settings.json`**, not in the browser. A timezone is
only useful if the CLI, the hooks and any cron reading the same brain agree with
the page that set it, and `localStorage` cannot do that. Four keys, all optional:

| Key | Effect |
|---|---|
| `tz` | IANA name deciding which calendar day a due date falls on (`today`, `week`, overdue) |
| `realm` | default realm for new nodes when nothing else says |
| `map_poll_seconds` | how often the map re-reads `/api/graph` (default 2) |
| `dash_refresh_seconds` | how often the dashboard re-runs its view (default 120) |

Precedence is the same everywhere in the engine: **explicit flag > env var > this
file > built-in default**. `GET /api/settings` returns the stored values, the
`effective` ones, and an `env` map naming every key a shell variable has taken
over — the Settings page shows that as a warning rather than pretending the save
took effect.

`realm` is re-resolved in-process on save, so it applies to the next write instead
of after a restart.

**The neo4j password is never sent to the browser.** `/api/status` reports
`password_set` only, and an empty `password` on `POST /api/neo4j-config` means
"keep the stored one". The file is written mode 600.

---

## Reload

The server restarts itself when its own code changes:

```bash
python3 viz/serve.py                 # reload on, the default
python3 viz/serve.py --no-reload     # off
```

This exists because of a failure that is genuinely hard to diagnose from the
browser. `index.html` is read from disk on **every request**, but `serve.py`,
`graph.py` and `brain_query.py` are imported **once at startup**. A server left
running for hours therefore serves a CURRENT page against a STALE API: the page
asks for a facet the process has never heard of, gets nothing back, and renders an
empty filter that looks broken rather than out of date.

How it behaves:

- Polls the mtimes of those four files once a second. No watch dependency for four
  files.
- On change it re-execs with the same argv, so the pid is unchanged and the port is
  rebound after the listening socket closes. It does not reopen the browser.
- **It refuses to restart into code that does not compile.** A typo mid-edit prints
  `reload held — does not compile: serve.py:112: invalid syntax` and the server
  keeps serving; fix the file and it restarts on the next poll.
- Every process has a `boot` id, returned in `/api/meta` and `meta.boot` of
  `/api/graph`. The page compares it on each poll and reloads itself when it
  changes, so an open tab picks up the new page without being touched.

If a facet ever renders "not sent by this server", that is this problem with
`--no-reload`, or a server predating the feature. Restart it.

## What the UI does with it

Two tabs over one dataset:

- **Map** — a hand-written force simulation (`alpha *= 0.985` per frame, so it
  needs a moment to settle). Legend chips filter by **node type** and by
  **realm** and **kind**, independently, each with its own `all` reset; clicking a node shows
  its neighbourhood. High-volume types (`channel`, `capture`) are worth hiding to
  make the rest legible.
- **Dashboard** — the date views, with a "Mine · needs action" toggle, one chip
  per assignee, one per realm, one per kind that has open tasks, and `blocked_by` / `blocks` / `related` badges from
  the edges. Cards expand on click (`/api/node`), and the date chip opens a picker
  that writes via `/api/set-due`.

### drop unsupported

The map's second toggle turns the filters from *hiding* into *unwinding*. Normally
hiding a type or realm leaves its dependants floating with nothing attached. With
**drop unsupported** on, a node survives only while at least one edge still points
at it from something visible. A node nothing points at is a root, and only the
chips can hide it.

```
A -> B -> C -> D,  E -> C

hide A        B goes with it (A was its only support).
              C survives on E, so D does too.
hide A and E  B falls, then C, then D — the whole chain unwinds.
```

It runs to a fixpoint, because order matters: C has to fall before D can. Cycles
hold each other up and so are never cascaded away on their own; a self-loop does
not count as support. The result is cached in `cascadeOff` and recomputed only
when a filter or the graph changes — never per frame. On an 830-node / 1393-edge
brain a full recompute is under a millisecond.

The chip's counter shows how many extra nodes the cascade took, so the toggle's
effect is legible rather than mysterious.

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
