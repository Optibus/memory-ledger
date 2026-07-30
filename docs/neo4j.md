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
| Resident memory (measured, same brain) | **26 MiB** | **836–862 MiB** |
| Read latency (measured, same brain) | **0.03–0.07 ms** in-process | **7–17 ms** over localhost HTTP |
| Cost at 250k nodes | broad search ~440 ms (see [performance](performance.md#scaling)) | indexed, and traversal is its native case |

**Measured, so do not guess at this.** The real brain (623 nodes) synced into a
local neo4j costs ~850 MiB resident and 7–17 ms per query, against 26 MiB and
0.03 ms for SQLite. That cost is **fixed** — it comes from the JVM heap, the page
cache and an HTTP round trip, not from graph size, so it does not amortise as the
graph grows. Full table in [comparison.md](comparison.md#measured-sqlite-vs-neo4j-same-brain-same-questions).

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

### 1. Run a local neo4j

```bash
docker run -d --name brain-neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/<a-strong-password> \
  -v "$HOME/.second_brain/neo4j-data:/data" \
  neo4j:5
```

Browser at <http://localhost:7474>. The HTTP transactional endpoint this project
uses is on **7474**, not the Bolt port — the code speaks HTTP with stdlib only, so
there is no driver to install.

### 2. Point the brain at it

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

### 3. Backfill and verify

```bash
python3 scripts/brain_sync.py --dry-run    # show what would be sent
python3 scripts/brain_sync.py              # push everything
python3 scripts/graph.py neo4j-status      # reachable? outbox depth?
python3 scripts/graph.py flush             # drain pending writes
```

`brain_sync.py` is idempotent — it `MERGE`s on `id`, so re-running updates rather
than duplicating.

### 4. Read from it

```bash
python3 scripts/brain_query.py agenda --backend neo4j
python3 scripts/brain_query.py open --backend auto     # neo4j if up, else sqlite
```

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

---

## Rolling back

There is nothing to undo. Remove `neo4j.json` (or unset the env vars) and the
brain goes back to SQLite-only; the outbox stops draining and `neo4j-status`
reports it down. The SQLite mirror was never behind — it is written first, every
time.
