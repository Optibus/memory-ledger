# Running neo4j instead of (alongside) SQLite

SQLite is the default because it needs nothing: one file, no server, no
dependencies, and it is fast up to a size a personal brain takes years to reach.
But the data model here **is** a property graph, and past a certain point a real
graph database is the better home for it.

The design supports both: **SQLite is the mirror, neo4j is the master.**

```
writes ──▶ SQLite (always) ──▶ outbox ──▶ flush ──▶ neo4j (if configured)
reads  ◀── SQLite (default)
reads  ◀── neo4j            (brain_query.py --backend neo4j)
```

Nothing breaks if neo4j is absent — the outbox simply accumulates and
`neo4j-status` reports it as down.

---

## Why you might want it

| | SQLite | neo4j |
|---|---|---|
| Setup | none | a server (Docker is one command) |
| Multi-hop traversal | one SQL query per frontier node, in Python | native, one Cypher query |
| Variable-depth paths | not expressible | `-[:REL*1..5]->` |
| Shortest path / centrality | hand-rolled | built in (GDS) |
| Ad-hoc graph questions | rewrite Python | write Cypher |
| Concurrent writers | single-writer, `database is locked` | proper transactions |
| Visual exploration | the bundled map UI | neo4j Browser / Bloom |
| Backup | copy a file | standard dumps |
| Resident memory (measured, same brain) | **26 MiB** | **~430–460 MiB** (492 MiB empty) |
| Read latency (measured, same brain) | **0.03–0.07 ms** in-process | **7–17 ms** over localhost HTTP |
| Cost at 250k nodes | broad search ~440 ms (see [performance](performance.md#scaling)) | indexed, and traversal is its native case |

**Measured, so do not guess at this.** The real brain synced into a local neo4j
costs ~450 MiB resident and 7–17 ms per query, against 26 MiB and 0.03 ms for
SQLite. That cost is **fixed** — it comes from the JVM heap, the page cache and an
HTTP round trip, not from graph size, so it does not amortise as the graph grows.
An empty container with zero nodes already costs 492 MiB and 516 MB of disk; the
brain's 647 nodes add 736 KB. Tuning `heap_max` and `pagecache_size` down moves the
floor. Full table in [comparison.md](comparison.md#measured-sqlite-vs-neo4j-same-brain-same-questions).

The honest summary: **for one person's brain, SQLite is enough and the map UI is
good enough.** The reason to add neo4j is **query shape, not node count** — the
things SQLite cannot express at all:

| Question | SQLite | neo4j (measured) |
|---|---|---|
| Variable-depth path 1..4 from a node | not expressible | 11.9 ms |
| Shortest path between two nodes | not expressible | 6.2 ms |
| 2-hop neighbourhood | needs application code | 11.8 ms, one query |
| Degree ranking / hub detection | correlated subquery | 8.0 ms |
| Orphan nodes | needs code | 5.5 ms |

Those are the argument. "Faster at scale" is not — SQLite wins every simple read
by two orders of magnitude at this size. Because the sync is one-way and the local
mirror is always written first, the sane pattern is to **turn neo4j on for an
afternoon of graph questions and turn it off again**.

---

## Setting it up

### The one command

```bash
python3 scripts/neo4j_local.py up      # or /neo4j-up inside Claude Code
```

That does the whole of this page's manual setup, idempotently:

| | |
|---|---|
| store | `~/.second_brain/neo4j/{data,logs}` — a bind mount inside the brain home, so the graph is a directory you can back up beside `graph.db` |
| container | `neo4j:5` named `second-brain-neo4j`, **`--restart unless-stopped`** (survives reboots, stays down when you stop it deliberately) |
| ports | `127.0.0.1:7474` (HTTP) and `127.0.0.1:7687` (Bolt) — **localhost only**, because the HTTP endpoint is cleartext |
| memory | heap 512m, page cache 256m (`--heap` / `--pagecache`) — deliberately modest; see the cost table above |
| auth | a password generated once into `~/.second_brain/neo4j.json` (chmod 600) and **never printed**. It reaches docker through the environment, not argv, so it stays out of the process list |
| config | writes `neo4j.json` and sets `backend: auto` — see "The `backend` setting" below |
| backfill | runs `brain_sync.py` and drains the outbox, so the new master is not an empty graph |

It **refuses to silently repoint an existing master**: if `neo4j.json` already
names a different host, it stops and tells you to pass `--switch` (repoint, old
config backed up first) or `--keep-config` (run the container, leave config
alone). Other flags: `--http-port` / `--bolt-port` (port taken), `--image`,
`--name`, `--bind` (only widen it behind TLS), `--password`, `--no-sync`,
`--default-backend`, `--dry-run`.

Then:

```bash
python3 scripts/neo4j_local.py status               # container + config + outbox
python3 scripts/neo4j_local.py down                # stop it; it stays stopped
python3 scripts/neo4j_local.py down --remove        # drop the container, keep the graph
```

`down` leaves the config in place: with `backend: auto` reads fall back to the
SQLite mirror and writes queue in the outbox, draining on the next `up`. That is
the pattern this page recommends — **on for an afternoon of graph questions, off
again.**

Browser at <http://localhost:7474>. The HTTP transactional endpoint this project
uses is on **7474**, not the Bolt port — the code speaks HTTP with stdlib only, so
there is no driver to install.

### Doing it by hand

The equivalent of what the command runs, if you would rather own it:

```bash
mkdir -p "$HOME/.second_brain/neo4j/data" "$HOME/.second_brain/neo4j/logs"
docker run -d --name second-brain-neo4j \
  --restart unless-stopped \
  --user "$(id -u):$(id -g)" \
  -p 127.0.0.1:7474:7474 -p 127.0.0.1:7687:7687 \
  -e NEO4J_AUTH=neo4j/<a-strong-password> \
  -v "$HOME/.second_brain/neo4j/data:/data" \
  -v "$HOME/.second_brain/neo4j/logs:/logs" \
  neo4j:5
```

`--user` is not optional with bind mounts: the image's `neo4j` user (uid 7474)
cannot write a directory owned by you, and the container crash-loops with
"Folder /logs is not accessible for user". The other trap is a store path the
Docker VM cannot share — keep it under your home directory.

### Point the brain at it

Config lives in the **private brain home**, never in the repo:

```bash
cat > ~/.second_brain/neo4j.json <<'JSON'
{
  "url": "http://localhost:7474",
  "db": "neo4j",
  "user": "neo4j",
  "password": "<a-strong-password>"
}
JSON
chmod 600 ~/.second_brain/neo4j.json
```

Or by environment, which wins over the file:

```bash
export SECOND_BRAIN_NEO4J_URL=http://localhost:7474
export SECOND_BRAIN_NEO4J_DB=neo4j
export SECOND_BRAIN_NEO4J_USER=neo4j
export SECOND_BRAIN_NEO4J_PASSWORD=...
```

(Both are what `neo4j_local.py up` writes for you — same file, same shape the
UI's **Settings › Database connection** writes.)

### Backfill and verify

```bash
python3 scripts/brain_sync.py --dry-run    # show what would be sent
python3 scripts/brain_sync.py              # push everything
python3 scripts/brain_sync.py --verify     # does the master hold ALL of it? exit 1 on a gap
python3 scripts/graph.py neo4j-status      # reachable? outbox depth?
python3 scripts/graph.py flush             # drain pending writes
```

`brain_sync.py` is idempotent — it `MERGE`s on `id`, so re-running updates rather
than duplicating. It never overwrites a node the master holds a NEWER `updated`
for, so a stale machine cannot roll back another one's edit.

### The road off SQLite

SQLite is still the source of truth today, and the plan is to retire it: neo4j
becomes the only store. Everything below exists to make that switch a small step
instead of a migration:

1. **neo4j holds everything SQLite holds**, losslessly — nothing lives only in
   the local file (*What the master holds*).
2. **Every write reaches neo4j**, whichever code path makes it (SQLite triggers).
3. **SQLite can be fed FROM neo4j** — incrementally (`sync`) or from nothing
   (`--pull`). Once a write made directly in neo4j flows down, the engine can
   start writing to neo4j first and SQLite becomes a cache, then goes away.
4. **`--verify` proves the two agree**, so each step can be checked, not trusted.

Still to do before SQLite can go: the engine's reads and writes are SQL
(`graph.py`), full-text search is SQLite FTS5, and the side tables travel as
`:MirrorRow` JSON rather than labels of their own.

### What the master holds

The master is a **complete copy** of the local file — enough to rebuild the
brain from nothing.

| on the master | what |
|---|---|
| `:Node` | every node. Props flattened for queries, and verbatim in `_props` for restore (a prop named like a column, e.g. `status`, survives only there) |
| `:REL` | every edge, with its props (`_props`) and `created` |
| `:MirrorRow {uid, table, key, data}` | every row of `graph.MIRROR_TABLES`: trigger requests/actions/gates/channels/checkpoints and the co-activation tally, as JSON |
| `:MirrorFile {path, content, sha}` | the home's own files: `settings.json`, `ui_prefs.json`, `eval_cases.json`, `README.md`, `scripts/`, `references/`, `learned_skills/` |

Not mirrored: secrets (`neo4j.json`, `trigger.json` — keep those in a password
manager), `blocked_item` (a projection, rebuilt on restore), and derived folders
(`exports/`, `bundles/`, `backups/`, `logs/`).

Every change reaches the outbox through **SQLite triggers** on those tables, so a
write that bypasses the engine's API (direct SQL, bulk ingest, the trigger engine)
is still mirrored. The session-start hook also runs a full `brain_sync.py` once a
day, which is what carries the files.

### Two-way sync

SQLite syncs **up and down** with the master:

```bash
python3 scripts/brain_sync.py --sync     # or: python3 scripts/graph.py sync
```

Pull first, then push. It runs by itself **every 60 s in the brain server**
(`$MEMORY_LEDGER_SYNC_SECONDS`; off for a `--no-write` server) and, detached,
**at every session start**.

How it knows what is new: every write to the master takes the next number from
one `:SyncClock` node and stamps it (`_seq`) with the writer that made it
(`_origin`); a delete leaves a `:Tombstone` with the same stamp. A local file
pulls everything after the last number it saw, skipping its own writes. Pulled
changes are applied with the mirror triggers muted, so they are never sent back.

**The write contract.** Anything that writes to neo4j directly — the engine once
it writes there first, a script, a person in the browser — must stamp the write
the same way (take `_seq` from `:SyncClock`, set `_origin`, delete through a
`:Tombstone`). The statements in `graph.py` (`_NODE_UPSERT`, `_NODE_DEL`, …) are
the reference. An unstamped write is invisible to `sync` and only reaches SQLite
through a full `--pull`.

| conflict | who wins |
|---|---|
| a node changed on both sides | the newer `updated`, on both |
| an edge or row with a change still queued here | the local one (they carry no time) |
| a delete against an edit | the edit — nothing is lost to a race |
| a file edited on both sides | the local file stays; the other copy lands beside it as `<name>.conflict-<writer>` (never mirrored itself) |

`neo4j-status` shows this file's writer id, how far it has pulled, and the last sync.

### Restore from it

```bash
python3 scripts/brain_sync.py --pull              # rebuild into graph.db.restored-<ts>, touch nothing
python3 scripts/brain_sync.py --pull --replace    # ...and swap it in; the old file goes to backups/
```

The restore refuses an empty master, integrity-checks the new file before any
swap, and writes home files only where none exist (`--overwrite-files` to
replace them).

### Read from it

```bash
python3 scripts/brain_query.py agenda --backend neo4j
python3 scripts/brain_query.py open --backend auto     # neo4j if up, else sqlite
```

### The `backend` setting

With no `--backend` flag, `brain_query.py` reads the **`backend` setting** in
`~/.second_brain/settings.json` (env `$SECOND_BRAIN_BACKEND` wins), falling back
to `sqlite`. So the default is machine-wide — the CLI, the hooks and any cron
agree, rather than each caller passing a flag.

| value | means |
|---|---|
| `sqlite` | always the local mirror (the default when nothing is set) |
| `auto` | neo4j when it answers, the mirror when it does not — what `neo4j_local.py up` writes |
| `neo4j` | master only; reads **fail** when it is down |

Set it in the UI (**Settings › Database connection › default read backend**) or
by `up`. Two deliberate behaviours: clearing the neo4j config from the Settings
page resets this to `sqlite` (a `neo4j` default with no master would break every
read), and `graph.py` itself always reads the local mirror regardless — the mirror
is written first on every mutation, so it is never behind. The map and dashboard
always prefer the master when it answers, whatever this says.

---

## How the sync works

Every write goes through `_after_write()`, which enqueues the affected node and
edge ids in the `outbox` table. `_flush()` drains it, batching into two Cypher
statements:

```cypher
UNWIND $nodes AS n MERGE (x:Node {id:n.id}) SET x += n
UNWIND $edges AS e MATCH (a:Node {id:e.src}), (b:Node {id:e.dst})
       MERGE (a)-[r:REL {rel:e.rel}]->(b) SET r += e.props
```

Everything is one label (`:Node`) with a `type` property, and one relationship
type (`:REL`) with a `rel` property. That keeps the sync trivial and schema-free.
The trade-off is that Cypher matches read a little less naturally
(`MATCH (:Node {type:'task'})-[:REL {rel:'blocked-by'}]->()` rather than
`MATCH (:Task)-[:BLOCKED_BY]->()`), and neo4j cannot use label indexes to narrow
by type. If you go neo4j-primary and care, projecting real labels and
relationship types is the obvious first improvement.

**Two deliberate non-syncs:**

- `_touch()` (strength reinforcement on read) is **local-only** — a read storm
  must not become a write storm. Strength rides along on the node's next real
  write.
- The `coactivation` Hebbian tally is local-only too; only the *edges* it
  eventually produces sync.

**If you write straight to SQLite you bypass the outbox** and the two stores
drift. Go through the `graph.py` commands or `graph.api_*` helpers.

---

## Useful Cypher once it is there

Given the single-label model:

```cypher
// everything blocking an open task
MATCH (t:Node {type:'task'})-[:REL {rel:'blocked-by'}]->(b)
WHERE t.status = 'open' RETURN t.title, collect(b.title);

// how a decision connects to an incident, any distance up to 5
MATCH p = shortestPath(
  (a:Node {id:$from})-[:REL*1..5]-(b:Node {id:$to})
) RETURN [n IN nodes(p) | n.title];

// hubs — the nodes the ubiquity penalty is protecting you from
MATCH (n:Node)--() RETURN n.type, n.title, count(*) AS deg
ORDER BY deg DESC LIMIT 20;

// orphans worth linking
MATCH (n:Node) WHERE NOT (n)--() RETURN n.type, n.title;

// everything in a domain area
MATCH (n)-[:REL {rel:'part-of'}]->(:Node {id:'hub_process'}) RETURN n.title;
```

The first and last are things `graph.py` already does. The middle two are the ones
you cannot express in the SQLite version without writing code, and they are the
actual argument for neo4j.

---

## Security

- **Plain HTTP sends credentials and your entire brain in cleartext.** The code
  says so explicitly. For anything beyond localhost, put TLS in front of it — a
  reverse proxy or a tunnel — before treating it as secure.
- The brain is personal data: meetings, decisions, colleagues, tickets. If it goes
  on a shared host, that is a different privacy posture than a local file, and it
  deserves a deliberate decision rather than a default.
- Keep `neo4j.json` at `chmod 600`. It holds a password in plaintext.
- Do not commit it — it lives in `~/.second_brain/`, outside the repo, and the
  repo ignores the brain home for exactly this reason.
- `neo4j_local.py` binds to `127.0.0.1` by default and generates the password
  itself so you never type it. It also **never prints it** — neither does
  `/neo4j-up`, because every prompt and answer in a session is captured into the
  graph, and a printed password would become a stored one. Read it out of
  `neo4j.json` when you need it for the browser.
- Widening `--bind` publishes cleartext credentials on your network. Put TLS in
  front of it first.

---

## Rolling back

There is nothing to undo. Remove `neo4j.json` (or unset the env vars) and the
brain goes back to SQLite-only; the outbox stops draining and `neo4j-status`
reports it down. The SQLite mirror was never behind — it is written first, every
time. (Clearing it from the Settings page also resets `backend` to `sqlite`.)

For the local container specifically: `neo4j_local.py down` stops it,
`down --remove` deletes the container while leaving the graph in
`~/.second_brain/neo4j` for a later `up`, and deleting that directory removes the
neo4j copy entirely — the SQLite brain is untouched, and the next `up` refills a
fresh store from it.
