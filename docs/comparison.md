# How this compares — MemPalace, and the storage backends

Two different questions get confused with each other:

1. **Which second brain?** memory-ledger vs MemPalace vs the rest.
2. **Which storage engine?** SQLite vs neo4j vs a vector store.

They are separable, and the answers are different. Everything below was measured
on 2026-07-30 on the same machine, with the recipes noted.

---

## The short version

**MemPalace and memory-ledger are not competing implementations of the same idea.**
They answer different questions and the difference is architectural, not
incidental.

| | memory-ledger | MemPalace |
|---|---|---|
| **The question it answers** | *What do I owe, to whom, by when?* | *Why did we do X? How did we handle Y?* |
| Direction | **prospective** — records state as you work | **retrospective** — mines what already happened |
| Substrate | SQLite + FTS5 | ChromaDB (vector store) |
| Retrieval | lexical + graph activation | semantic (embeddings) |
| Unit | typed node (`task`, `decision`, `person`…) with `status`, `due`, `assignee` | "drawer" in a wing/room, plus a diary and a KG |
| Open loops | first-class — the point of it | not modelled |
| Acts on the world | yes — Slack, Gmail, Jira, calendar, reminders | no — it is a memory, not an agent |
| Finds things you cannot name | no | **yes** — this is the real advantage |
| Interface | plugin: hooks + skills + CLI + local dashboard | ~19 MCP tools + hooks |

The one-line difference: **MemPalace can tell you what was decided; it cannot tell
you that Isadora is waiting on you.** memory-ledger tracks *state* — status, due
dates, who owes what, what is stale. MemPalace tracks *history*, and searches it by
meaning rather than by keyword, which is something this project deliberately
cannot do.

They are complements, and Ilan's own onboarding kit says as much ("you can run more
than one — just don't duplicate the same facts across both"). If you want one
sentence of advice: **run MemPalace if your pain is "we solved this before and I
can't find it"; run memory-ledger if your pain is "I've lost track of what I owe
people".**

---

## Measured: footprint and read cost

Same machine, same day. memory-ledger measured on the real brain (623 nodes / 909
edges); ChromaDB measured on synthetic corpora; neo4j measured with the real brain
synced into it.

| | memory-ledger (SQLite) | ChromaDB @ 50k docs | neo4j @ 623 nodes |
|---|---|---|---|
| Resident memory | **26 MiB** | **262 MiB** | **836–862 MiB** |
| — of which baseline | ~10 MiB (bare Python) | 76 MiB (import only) | 512 m heap + 256 m pagecache configured |
| Disk | **1.60 MiB** | 291 MiB | 516 MB (`/data`) |
| Per unit | 2.7 KB/node | ~6 KB/doc | — |
| Read latency | **0.03–0.07 ms** in-process | ~1.0 ms vector query | 7–17 ms over localhost HTTP |
| Write cost | 0 model calls, ~60 ms/hook | 29 s per 50k docs *before* embedding | 2.6 s for 623 nodes |

Reading that table honestly:

- **The memory gap is real and large.** 26 MiB against 262 MiB against ~850 MiB.
  For a personal tool running all day on a laptop alongside an IDE and a browser,
  that is the difference between unnoticeable and noticeable.
- **The ChromaDB figure is a lower bound.** That measurement used precomputed
  random vectors, so it excludes the embedding model a real MemPalace loads
  (~80–90 MB of ONNX weights plus its runtime) and the CPU spent embedding every
  document. Ilan's note that mining a long history "takes real time (minutes to
  much longer)" is that cost showing up.
- **Vector search itself is fast.** ~1 ms at 50k documents is perfectly good — the
  cost of MemPalace is resident memory and write time, not query speed. Be fair
  about that.
- **neo4j's latency here is mostly the round trip**, not the engine: HTTP plus a
  transaction plus JSON, per query. It is not 200× slower at graph work; it is
  200× slower at *answering a trivial question from another process*.

### Extrapolating to a real palace

Ilan reports **~252,000 drawers / ~2.5 GB**. Two checks against that:

- My synthetic corpus extrapolates to ~1.43 GiB at 252k documents. He reports
  2.5 GB, so his drawers are roughly 1.7× my synthetic ones — the same order,
  which means the measurement is a reasonable stand-in.
- The HNSW index alone, by construction: 252,000 vectors × 384 dims × 4 bytes =
  **369 MiB raw**, and an HNSW graph typically costs 1.5–2× that on top, so
  **~550–740 MiB resident** while the index is being queried. That is arithmetic,
  not a measurement, but it is not an estimate either — it follows from the vector
  count and dimensionality.

For comparison, memory-ledger at its observed growth rate reaches ~20k nodes and
~55 MiB **after a year** (see [performance](performance.md#growth-and-long-term-storage)).
The two systems are two to three orders of magnitude apart in size, because they
are keeping different things: MemPalace keeps everything it can mine, and
memory-ledger keeps what was worth typing.

---

## Measured: SQLite vs neo4j, same brain, same questions

The real brain synced into a local neo4j (Docker, 512 m heap) and asked the same
questions both ways:

| Question | SQLite (in-process) | neo4j (localhost HTTP) |
|---|---|---|
| Count all nodes | 0.039 ms | 8.7 ms |
| Open tasks with a due date | 0.070 ms | 16.7 ms |
| 1-hop neighbours of a hub | 0.030 ms | 7.0 ms |
| Text contains "depot" | 0.048 ms (FTS5) | 12.9 ms (`CONTAINS` scan) |
| **2-hop neighbourhood** | needs application code | **11.8 ms, one query** |
| **Variable-depth path 1..4** | **not expressible** | **11.9 ms** |
| **Shortest path between two nodes** | **not expressible** | **6.2 ms** |
| Degree ranking (hub detection) | needs a correlated subquery | 8.0 ms |
| Orphan nodes | needs code | 5.5 ms |

**This changes the recommendation.** The earlier advice — "move to neo4j past
~250k nodes" — was wrong in its emphasis. neo4j's cost is a **fixed ~850 MiB of
resident memory and a 7–17 ms round trip on every query, regardless of graph
size**. It does not become cheaper as the graph grows; it starts expensive and
stays flat.

So the crossover is about **query shape, not node count**:

- Stay on SQLite for everything the CLI, the dashboard and `recall` already do.
  At 623 nodes — and at 60,000 — it is hundreds of times cheaper per read and
  ~30× lighter in memory.
- Add neo4j when you want the bottom four rows of that table: variable-depth
  paths, shortest path, centrality, orphan and hub analysis. Those are not "SQLite
  is slower at this" — they are *not expressible* without writing traversal code.
- Because the sync is one-way and the local mirror is always written first, adding
  neo4j costs nothing but the container. Turn it on for an afternoon of graph
  questions, turn it off after. See [neo4j.md](neo4j.md).

---

## Where each design is genuinely better

**MemPalace is better at:**

- Finding something you cannot name. Semantic search over months of transcripts is
  a real capability this project does not have and cannot get without an embedding
  step.
- Bootstrapping from history. `/mempalace:mine` gives you a useful store on day
  one; memory-ledger starts empty and fills as you work.
- Breadth. It keeps everything it can reach, so the answer is probably in there
  somewhere.

**memory-ledger is better at:**

- State, not just recall. `status`, `due`, `assignee`, `waiting_on`, `blocked_by` —
  and therefore "what's overdue", "what's stale", "who owes me what". A vector
  store has no notion of an open loop.
- Structure you can query exactly. Typed nodes and typed edges mean
  `list --type task --status open` is precise, not probabilistic.
- Acting. It sends the Slack message, moves the Jira ticket, books the calendar
  slot, sets the reminder. It is an agent with a memory, not a memory.
- Footprint. 26 MiB and one 1.6 MiB file you can copy, inspect with any SQLite
  tool, or delete.
- Auditability. Every ranking decision is arithmetic you can read in
  [algorithm.md](algorithm.md). Embedding similarity is not inspectable in the
  same way.

**Both share:** local-first, no cloud, no API key for retrieval, automatic capture
via hooks, and a knowledge-graph layer.

---

## A note on token cost

Neither system spends model tokens *computing* retrieval — MemPalace embeds
locally, memory-ledger uses SQL. Both spend tokens on the results that land in
context, which is unavoidable.

One structural difference worth knowing: MemPalace exposes ~19 MCP tools, and MCP
tool schemas occupy context on every request unless the client defers them.
memory-ledger adds no tools — it uses hooks that inject a fixed 208 bytes per prompt
plus skills that load only when invoked, and its read paths can be run **entirely
off-model** in a terminal or the dashboard, where you read the answer yourself and
the model never sees it. I have not measured MemPalace's schema overhead, so treat
this as a consideration to check rather than a number.

---

## Reproducing these measurements

```bash
# neo4j, same brain, both backends
docker run -d --name brain-neo4j-bench -p 7475:7474 -p 7688:7687 \
  -e NEO4J_AUTH=neo4j/<pw> -e NEO4J_server_memory_heap_max__size=512m \
  -e NEO4J_server_memory_pagecache_size=256m neo4j:5
export SECOND_BRAIN_NEO4J_URL=http://localhost:7475 \
       SECOND_BRAIN_NEO4J_USER=neo4j SECOND_BRAIN_NEO4J_PASSWORD=<pw>
python3 scripts/brain_sync.py
docker stats --no-stream brain-neo4j-bench          # resident memory
docker exec brain-neo4j-bench du -sh /data          # disk

# SQLite resident memory: RSS of a process that opens the db and runs one recall
python3 -c "import sys,resource;sys.path.insert(0,'scripts');import graph;\
c=graph.connect();c.execute('SELECT COUNT(*) FROM nodes').fetchall();\
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024/1024)"

# ChromaDB: build collections of 1k/10k/50k precomputed 384-d vectors in a venv,
# then read ru_maxrss and du after each. Random vectors isolate the store from the
# embedding model, which makes the result a deliberate lower bound.
```

Full latency, storage and growth numbers for memory-ledger itself:
[performance.md](performance.md).
