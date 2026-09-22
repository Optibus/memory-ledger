# HTTP API

`scripts/serve.py` is a dependency-free stdlib HTTP server. It serves two things
over one port: this JSON API, and the React single-page app built from
`frontend/`. It calls **no model** — it is the graph engine behind a socket, so
everything it does is token-free.

Neither half needs a toolchain to run: `frontend/dist` is committed, so a machine
with no `npm` serves the same page as any other. When the source IS newer than
the bundle and npm is there, the server rebuilds it before binding the port;
`--no-build` skips that and `--rebuild` forces it.

```bash
python3 scripts/serve.py                    # http://127.0.0.1:8770
python3 scripts/serve.py --port 9000 --host 127.0.0.1
python3 scripts/serve.py --no-write         # read-only
python3 scripts/serve.py --no-open          # don't open a browser
```

## The interactive reference

**This file is the prose; [`/api/docs`](http://127.0.0.1:8770/api/docs) is the
reference.** Start the server and open it: Swagger UI over an OpenAPI 3.1
document with every endpoint, every parameter, every response shape, and a
Try-it-out button that hits your own brain.

| Where | What |
|---|---|
| `GET /api/docs` | Swagger UI. The viewer loads from a CDN; the document does not. |
| `GET /api/openapi.json` | The document itself, built by the running process. |
| `docs/openapi.json` | The same document on disk, for reading without a server. |
| `scripts/openapi.py` | Where it is written. Run it with `--out` to regenerate the file. |

The document is **built by the process that answers the requests**, so what you
browse always describes the server you are talking to. And it cannot drift from
the router: `tests/test_openapi.py` reads serve.py's actual `do_GET` chain and
`do_POST` dispatch table out of the source and fails if either side holds an
endpoint the other has never heard of. Documentation nobody checks is a rumour.

Adding an endpoint therefore means adding it in two places, and CI tells you
when you have done one of them:

```bash
python3 tests/test_openapi.py                      # the drift guard
python3 scripts/openapi.py --out docs/openapi.json # refresh the checked-in copy
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
| `/api/meta` | the vocabulary — node types, realms, kinds, edge rels, statuses, and the two contract shapes (`skill_sections`, `rule_sections`, `decl_labels`, `node_types`) |
| `/api/skills` | the two skill trees, the rule catalog, `node_types` (each with its description, how many of it the brain holds, and which skills read or write it), and the area titles the filters label themselves with |
| `/api/rule?id=<id>` | one rule in full, for the skill drawer |
| `/api/bundle?name=<file>` | an exported bundle, as a zip attachment. `name` is reduced to a bare filename, so it can only name a file in `<home>/bundles` |
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
| `/api/create-rule` | `{title, scenario, source?, section?, realm?, non_negotiable?, overrides?, sections{}}` | authors a rule (§5b). **Refused** unless `sections.the_rule` and `sections.judged_against` are filled |
| `/api/create-skill` | `{name, title, description?, realm?, sections{}, requires?, waits_for?, enriched_by?, reads?, writes?, rules[], new_rules[]}` | authors a skill (§5). **Refused** unless the six typed sections of §5.1 are filled. `rules[]` are existing rule ids, `new_rules[]` are whole rule bodies created first in the same call |
| `/api/export-skills` | `{kind?, dir?}` | `graph.py export-skills` — a `SKILL.md` per skill plus every rule they honor |
| `/api/ingest-skills` | `{dir?}` | `graph.py ingest-skills` — reads a folder back in; a rule this brain already holds is kept, never overwritten |
| `/api/export-bundle` | `{skills[], rules[]}` | `graph.py export-bundle` — a selection as one zip under `<home>/bundles`; returns `path`, `name`, a `download` URL, and any required skill that did not travel |
| `/api/upload-bundle` | `{filename, data}` | base64 zip, ≤32MB. Stages it and returns a one-shot `token` plus every item with `exists`. **Reads only — nothing is written** |
| `/api/import-bundle` | `{token, select{id: update\|replace\|ignore}}` | applies the staged bundle in its plan's order and consumes it. An unmentioned item is created when new, untouched when it exists |
| `/api/delete-contracts` | `{ids[], force?}` | `graph.py del-contracts` — deletes skill/rule nodes and their edges. A built-in is refused unless `force`, since its file would re-ingest it |

### Authoring contracts

The two create routes exist so the brain can grow without a model call, and they
are **stricter than the CLI**, not looser. `graph.py add-rule` accepts a half
contract and leaves the gap for `audit-contracts` to report; these refuse it at
write time, because a form has all the fields in front of it and no excuse to
submit one that cannot be checked.

The shapes come from `/api/meta` rather than being written into the page. A
heading typed into `index.html` would be a second definition of §5.1, and the one
nothing checks:

```json
"skill_sections": [
  {"field": "when_to_use",   "heading": "## When to use", "required": true,  "generated": false},
  {"field": "rules_honored", "heading": "## Rules honored", "required": false, "generated": true},
  …
]
```

`generated: true` marks the two sections nobody types — `## Rules honored` is
rendered from `rules[]`, `## Depends on` from the declaration fields — so the
form knows to leave them out.

The pickers those forms open are fed from `/api/skills`, which is why
`node_types` carries more than a list of names: a card that says only `gauge`
asks the person to already know what a gauge is, and the readers and writers are
what say which skills would become coupled to one that declares it.

A skill's rules land **twice on purpose**: as ids in the `## Rules honored` prose,
which is what a person reads when the skill fires, and as `honors` edges, which
is what the graph can walk (§5a). `new_rules[]` are written before the skill and a
failure in one aborts the whole call, so a skill is never left accountable to a
rule that does not exist.

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
python3 scripts/serve.py                 # reload on, the default
python3 scripts/serve.py --no-reload     # off
```

This exists because of a failure that is genuinely hard to diagnose from the
browser. The built page is read from disk on **every request**, but `serve.py`,
`graph.py`, `brain_query.py` and `openapi.py` are imported **once at startup**. A
server left running for hours therefore serves a CURRENT page against a STALE
API: the page asks for a facet the process has never heard of, gets nothing back,
and renders an empty filter that looks broken rather than out of date.

How it behaves:

- Polls the mtimes of those five files once a second. No watch dependency for
  five files.
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

### shade instead of hide

The filters pick **which** nodes fall out. This toggle picks what falling out
**looks like**, and the split matters because the two modes answer different
questions. Hiding answers *"get this out of my way"*: the node leaves the canvas,
drops out of the force simulation, and the layout closes the gap. Shading answers
*"show me just this"*: the node stays exactly where it is, dimmed to 20% with its
edges dimmed alongside it, and the graph keeps its shape so what you kept can be
read in context instead of floating alone.

One predicate feeds both, which is what keeps them honest:

```ts
// frontend/src/lib/graphFilter.ts
export function filteredOut(node, f, cascadeOff) {
  return directHidden(node, f) || cascadeOff.has(node.id);
}
export function isHidden(node, f, off) { return !f.shade && filteredOut(node, f, off); }
export function isShaded(node, f, off) { return  f.shade && filteredOut(node, f, off); }
```

It is a pure module with no React and no canvas in it, which is what makes the
property above testable rather than merely asserted:
`frontend/src/lib/graphFilter.test.ts` renders neither and checks that the two
modes reach exactly the same set.

Every filter — the three facet dropdowns, hide-done, and the cascade below —
computes `filteredOut` and nothing else, so none of them knows the mode exists and
flipping it can never change *which* nodes are affected, only how they are painted.

Two rules make shading readable rather than just dimmer:

- **A shaded node loses its label.** It keeps its dot and its edges, which is the
  shape it was kept for, but a few thousand dimmed labels over the handful you
  isolated is noise, not context. Selected, hovered and search-hit nodes keep theirs.
- **A search hit stays lit.** You asked for it by name; dimming it because a type
  filter also excluded it would read as *"no results"* rather than *"filtered out"*.

An edge is only as lit as its dimmer end — one shaded endpoint dims the edge —
or a shaded node would stay wired into the lit graph by full-strength lines and
read as part of it.

### drop unsupported

The map's other filter toggle turns the filters from *hiding* into *unwinding*. Normally
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

It runs on the filter, not on what is drawn, so shade mode reaches exactly the
same set — the unwound nodes are dimmed rather than removed. It runs to a
fixpoint, because order matters: C has to fall before D can. Cycles
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
5. **Describe it in `scripts/openapi.py`.** `tests/test_openapi.py` fails until
   you do, which is the point: the spec is generated from a table beside the
   router precisely so the two cannot be edited apart.

On the client side, add it to the named surface in
`frontend/src/api/client.ts` rather than calling `fetch` from a component. Every
call in the app goes through one `get`/`post` pair, so retry, error shaping, the
`--no-write` 403 and abort-on-unmount are decided once instead of at each of the
fifty call sites the old single-file page had.

That last point matters: writing straight to SQLite bypasses `_after_write()`,
which is what enqueues the neo4j sync. See [the neo4j backend](neo4j.md).
