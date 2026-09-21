# Performance, storage and limits

Every number here was measured, not estimated. The recipes to re-run each
measurement are included — treat the numbers as a snapshot of one real brain
(620 nodes / 906 edges, 9 days of daily use) and re-measure your own.

Measured 2026-07-30 · macOS · Python 3 · SQLite with FTS5.

---

## Token cost

The always-on half of the brain **never calls a model**. Verify:

```bash
grep -rn -iE "anthropic|api\.claude|openai|httpx" hooks/*.py scripts/*.py
# only neo4j HTTP transactional endpoints in graph.py / brain_query.py / brain_sync.py
```

What *does* reach the model is text the hooks inject into the conversation:

| Hook | Fires | Injected | Latency |
|---|---|---|---|
| `capture_prompt.py` | every prompt | 208 bytes (~52 tokens) | 64 ms |
| `open_loops_brief.py` | session start / clear / compact | ~1.0 KB (~267 tokens) | 185 ms |
| `capture_answer.py` | every answer | nothing (writes only) | 57 ms |

A heavy day of ~60 prompts across ~6 sessions:

| | Tokens/day | Cost/day at Opus 5 input ($5/MTok) |
|---|---|---|
| Per-prompt line | 60 × 52 = 3,120 | $0.016 |
| Session brief | 6 × 267 = 1,602 | $0.008 |
| **Total** | **~4,700** | **~$0.02–0.03** |

Against a real day of Claude Code on the same machine, that is **under 0.05%**.

```bash
npx ccusage@latest daily                     # your own spend, per day
echo '{"session_id":"m","cwd":"'"$PWD"'","prompt":"test"}' > /tmp/hookin.json
for h in open_loops_brief capture_prompt capture_answer; do
  printf '%s: %s bytes\n' "$h" \
    "$(python3 hooks/$h.py < /tmp/hookin.json | wc -c | tr -d ' ')"
done
```

The open-loops brief is capped at 6 loops, so it does not grow with the graph.

---

## Recall time

### Via the CLI (cold process — includes ~60 ms interpreter startup)

| Command | min | median |
|---|---|---|
| `graph.py recall "ops driver depot"` | 80 ms | 91 ms |
| `graph.py search "confluence"` | 75 ms | 76 ms |
| `graph.py open-loops` | 75 ms | 79 ms |
| `graph.py due` | 75 ms | 76 ms |
| `graph.py stats` | 73 ms | 75 ms |
| `graph.py match-rules "write a slack reply"` | 74 ms | 77 ms |
| `brain_query.py agenda` | 64 ms | 68 ms |
| `brain_query.py open --mine` | 68 ms | 68 ms |

**Python startup dominates.** Every command lands in the same 64–91 ms band
regardless of what it does, because the actual work is sub-millisecond:

### In-process (what the HTTP server pays per request)

| Query | min | median |
|---|---|---|
| FTS5 MATCH, 1 term | 0.030 ms | 0.034 ms |
| FTS5 MATCH, 3-term OR | 0.088 ms | 0.091 ms |
| Open tasks by status + due (indexed) | 0.067 ms | 0.069 ms |
| 1-hop neighbours of an ~80-edge hub | 0.030 ms | 0.030 ms |
| Full node scan | 0.524 ms | 0.540 ms |

So: a long-running process (the dashboard) answers in microseconds; the CLI costs
a process spawn. If you are scripting a loop over many queries, import
`graph.py` rather than shelling out per iteration.

---

## Storage

| | |
|---|---|
| File on disk | 1,679,360 bytes (1.60 MiB) |
| Nodes / edges | 620 / 906 |
| `sum(length(body))` | 289,056 bytes |
| `sum(length(props))` | 132,232 bytes |
| `sum(length(title))` | 35,477 bytes |
| **Payload per node** | **737 bytes** |
| **On-disk per node** | **2,709 bytes** |
| FTS index | 344,921 bytes (21% of the file) |

The ~3.7× gap between payload and on-disk is the FTS index, five B-tree indexes,
and SQLite's 4 KiB page granularity. It is the price of instant search on a file
you can copy.

```bash
python3 - <<'PY'
import os, sqlite3
db = os.path.expanduser("~/.second_brain/graph.db")
con = sqlite3.connect(db)
n = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
print(f"{os.path.getsize(db):,} bytes · {n:,} nodes · {os.path.getsize(db)/n:,.0f} B/node")
PY
```

---

## Growth and long-term storage

Observed over 9 calendar days (8 with writes), 620 nodes:

| Basis | Nodes/day | KiB/day | MiB/year |
|---|---|---|---|
| Median active day | 56.5 | 149 | 53 |
| Mean active day | 77.5 | 205 | 73 |
| Mean calendar day | 68.9 | 182 | 65 |

Type mix, all time:

| Type | Count | Share |
|---|---|---|
| `capture` | 213 | 34.4% |
| `task` | 116 | 18.7% |
| `channel` | 55 | 8.9% |
| `note` | 53 | 8.5% |
| `decision` | 45 | 7.3% |
| `jira-issue` | 26 | 4.2% |
| `skill` | 24 | 3.9% |
| `rule` | 21 | 3.4% |
| everything else | 67 | 10.7% |

**Read this with a caveat.** The window includes one-time bootstrapping — 53
`channel` nodes on a single day, 21 `rule` nodes when conventions were ingested,
24 `skill` nodes. Steady state is dominated by `capture` + `task`, so the honest
long-run estimate is **nearer 50 MiB/year than 73**, and it scales with how much
you use Claude Code rather than with wall-clock time.

At ~55 nodes/day: **~20k nodes and ~55 MiB after a year**, ~100k nodes and
~270 MiB after five. For a single-file local database, storage is a non-issue.
Query cost is the thing that changes — see below.

`consolidate` is the pressure valve: it digests the day's raw captures into
durable notes, which is why recall excludes captures by default. If growth ever
matters, captures are the type to prune.

---

## Scaling

Synthetic graphs built with the real schema, timing the exact query shapes
`recall` and `open-loops` use (`scale_test.py` recipe at the end):

| Nodes | Edges | MiB | FTS seed | Spread (d2) | Degree calc | Open-loops scan |
|---|---|---|---|---|---|---|
| 620 | 930 | 0.8 | 0.8 ms | 0.9 ms | 0.4 ms | 0.3 ms |
| 6,000 | 9,000 | 7.4 | 6.9 ms | 1.3 ms | 0.7 ms | 4.2 ms |
| 60,000 | 90,000 | 73.9 | 90 ms | 1.3 ms | 0.9 ms | 52 ms |
| 250,000 | 375,000 | 311 | 436 ms | 1.0 ms | 0.8 ms | 292 ms |

Three distinct behaviours:

**Spreading activation is flat.** ~1 ms at every size. It is bounded by
seeds (≤40) × degree × depth (2), with a 0.005 activation floor pruning weak
paths — not by graph size. The clever part of recall is the part that scales.

**The FTS seed scales with matches.** `ORDER BY rank` ranks every matching row
before `LIMIT 40` applies. This synthetic corpus is a **worst case**: bodies are
drawn from only 30 distinct words, so every term matches a large fraction of
documents. Real language is Zipfian and far more selective, so expect a much
flatter real curve — but the shape is real, and a deliberately broad cue on a
large graph will find it.

**Scans scale linearly.** `open-loops` is `status NOT IN ('done','cancelled')`,
which no index serves, so it is a full scan returning whole rows.

Practical reading: at today's size everything is instant. At ~60k nodes (roughly
three years at the observed rate) the slow paths reach 50–90 ms, still fine
behind a CLI that already costs 60 ms to start. Past ~250k nodes, a broad search
becomes perceptible, and that is the point to move to
[neo4j](neo4j.md) or add the indexes below.

---

## Resident memory, and how that compares

Measured on the same machine, same day — the real brain synced into a local neo4j,
and a ChromaDB store (the substrate MemPalace uses) built at increasing scale:

| | Resident memory | Disk | Read latency |
|---|---|---|---|
| **memory-ledger (SQLite)** | **26 MiB** | **1.60 MiB** | 0.03–0.07 ms in-process |
| neo4j, same brain | ~430–460 MiB steady state | 516 MB (736 KB is the graph) | 7–17 ms over localhost HTTP |
| ChromaDB, 50k documents | 262 MiB | 291 MB | ~1.0 ms |

~10 MiB of the SQLite figure is bare Python; 76 MiB of the ChromaDB figure is the
import alone. The ChromaDB number is a **lower bound** — it used precomputed
vectors, so it excludes the embedding model a real deployment loads.

**Read the neo4j row carefully — almost none of it is the graph.** Re-measured on
2026-07-30 with 647 nodes / 987 edges, and against an empty container as a control:

| Component | Size | What it is |
|---|---|---|
| JVM anon | 427 MiB | the engine: 144 MiB committed heap (109 used, 512 configured), 161 MiB non-heap, the rest off-heap page cache and thread stacks |
| OS page cache | 425 MiB → 22 MiB | the kernel caching the preallocated transaction logs; it was reclaimed on its own within a minute, unprompted |
| kernel slab | 12 MiB | |
| **the graph itself** | **736 KB on disk** | 647 nodes, 987 edges |

Two controls make the point. An **empty container that has never been synced** sits
at 492 MiB with a byte-identical 516 MB of disk — the cost is there before any data
is. And after the page cache was reclaimed, the synced container fell from 866 MiB
to **456 MiB** with nothing under memory pressure.

Mind the accounting: cgroup `memory.current` reads 866 MiB at peak while
`docker stats` reads ~450 MiB for the same container at the same instant, because
Docker subtracts inactive file cache. Earlier revisions of this page published the
peak figure (836–862 MiB) as the resident cost; the steady-state engine figure
above is the fair one. The disk figure is not storage in any useful sense either —
515 MB of the 516 MB is preallocated transaction logs, 258 MB each for the `neo4j`
and `system` databases.

The point is not that one wins. It is that the always-on footprint of this design
is small enough to ignore, which is what makes leaving it running a non-decision.
Full comparison, including which questions justify neo4j:
[comparison.md](comparison.md).

## Known limits

| Limit | Consequence | Mitigation |
|---|---|---|
| `props` is a JSON blob | cannot index or search inside it; assignee/priority filters are post-fetch in Python | add a generated column + index for any prop you filter on constantly |
| FTS covers `title` + `body` only | props text is invisible to search | put anything you need to find into the body |
| `ORDER BY rank` before `LIMIT` | broad cues cost O(matches) | narrower cues; a `status`-partitioned index; neo4j |
| `open-loops` / `due` full-scan | linear in node count | partial index on `status`, or archive aggressively |
| No stemming, no synonyms, no vectors | `deploys` ≠ `deploying`; a cue sharing no words finds nothing lexically | prefix terms cover some of it; edges cover the rest |
| `recall` writes (Hebbian) | a read can create ≤2 edges | `--no-wire` |
| Single-writer SQLite | concurrent writers can hit `database is locked` | one writer at a time; the outbox serialises neo4j pushes |
| Unauthenticated write API | anything reaching the port can rewrite the graph | bind `127.0.0.1`, or `--no-write` |
| Local file, no backup | disk loss loses the brain | `graph.py export`, or mirror to neo4j |

---

## Re-running the scale test

```python
# builds throwaway DBs with the real schema and times recall's query shapes
import sqlite3, sys, time, random, statistics
sys.path.insert(0, "scripts")
import graph
con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
graph._ensure_schema(con)
# ... insert N synthetic nodes/edges, then time:
#   the FTS seed   (SELECT ... nodes_fts MATCH ? ORDER BY rank LIMIT 40)
#   the spread     (neighbour query per frontier node, depth 2)
#   the degree calc(correlated COUNT over the activated id set)
#   the loop scan  (status NOT IN ('done','cancelled'))
```

Use bodies drawn from a realistic vocabulary — a small word pool makes every term
match everything and overstates the FTS cost by an order of magnitude.
