# The algorithm

How the brain decides what to keep, what to surface, and what to wire together.
Everything here runs in **pure Python + SQL** — no model call — which is why
recall is free to use as often as you like.

Read [`architecture`](#the-shape-of-it) first if you just want the shape;
[`recall`](#recall--associative-retrieval) is the interesting part.

---

## The shape of it

```
   hooks/                    scripts/graph.py                ~/.second_brain/
   capture_prompt.py  ─┐     ┌─ the engine ─────────┐        ┌──────────────┐
   capture_answer.py  ─┼───▶ │ nodes · edges · FTS5 │ ──────▶│  graph.db    │
   open_loops_brief.py ┘     │ recall · consolidate │        │  (SQLite)    │
                             └──────────┬───────────┘        └──────┬───────┘
   skills/  (model-driven,              │                           │ optional
   spend tokens when they         scripts/serve.py                      ▼ mirror
   reach out to Slack/Jira)       HTTP + dashboard             neo4j master
```

Three storage objects, and that is the whole data model:

| Table | Holds |
|---|---|
| `nodes` | `id, type, title, body, props (JSON), status, due, created, updated, realm`, plus `cues` — a VIRTUAL generated column pulling the match-only props (`scenario`, `triggers`, `description`) into the FTS index ([search](search.md)) |
| `edges` | `src, dst, rel, props` — unique on `(src, dst, rel)` |
| `nodes_fts` | FTS5 index over `title` + `body` + `cues`, kept in sync by triggers |

Plus two local-only side tables: `outbox` (pending neo4j syncs) and
`coactivation` (the Hebbian tally, below).

`props` being a JSON blob is deliberate — node types disagree about their fields,
and a migration per new field would be worse than a blob. The cost is that you
cannot index inside it; see [Known limits](performance.md#known-limits).

---

## Capture — writing without thinking about it

Three hooks, none of which call a model:

- **`capture_prompt.py`** (`UserPromptSubmit`) stores the prompt as a `capture`
  node and regex-detects explicit to-dos, creating `task` nodes for them. It
  returns a short `additionalContext` line telling the model the turn is already
  stored, so the model does not spend tokens re-storing it.
- **`capture_answer.py`** (`Stop`) stores a summary of the answer, linked back to
  its question. Injects nothing.
- **`open_loops_brief.py`** (`SessionStart`) injects the current open-loop count
  and the top few loops, so a fresh session starts with state instead of cold.

Assignee inference is heuristic and deliberately conservative: "ask/chase
\<Name\>" stays *yours* (you owe the chasing), while "\<Name\> will do X" is
theirs. Never blindly "me".

---

## Strength — the forgetting curve

Every node carries `access_count` and `last_accessed` in `props`. Strength is
computed, never stored as truth:

```python
ceiling  = min(1.0, 0.25 + 0.18 * sqrt(access_count))
strength = ceiling * 0.5 ** (days_since_access / 21.0)
```

- **Repetition raises the ceiling** — a node read ten times can reach a higher
  strength than one read once, sub-linearly.
- **Disuse decays toward it** — 21-day half-life (`STRENGTH_HALF_LIFE`).
- **Reading reinforces.** `_touch()` bumps `access_count` on every recall hit.
  It is intentionally local-only and does *not* enqueue a neo4j sync: a read
  storm must not become a write storm. Strength rides along on the node's next
  real write.

Nothing is ever deleted by decay. Strength changes *rank*, not existence — the
distinction that makes this safe to leave running.

---

## Recall — associative retrieval

`graph.py recall "<cue>"` is the main read path. One call replaces
`search` + `neighbors`, and costs fewer tokens than either.

### 1. Cue extraction

Words shorter than 3 characters are dropped, then a bilingual stop-list
(`_CUE_STOP`, English + pt-BR — the brain is driven in both) removes function
words. Each survivor becomes a prefix term. If stripping would leave nothing,
the original words are kept — an empty cue is worse than a noisy one.

### 2. Seeding

```sql
SELECT n.* FROM nodes_fts f JOIN nodes n ON n.rowid = f.rowid
WHERE nodes_fts MATCH ? ORDER BY rank LIMIT 40
```

Terms are OR-ed. Seed activation is `1 / (1 + rank_position)`, so the top FTS hit
starts at 1.0 and the 40th at ~0.024.

### 3. Spreading activation

Activation flows outward across edges for `ACTIVATION_DEPTH` (2) hops, retaining
`ACTIVATION_DECAY` (0.45) per hop, and **convergent paths sum** — a node reached
from two different seeds is more activated than one reached from either alone.
That is the "evidence adds up" property.

The critical detail is **fan-out normalisation**:

```python
share = activation * ACTIVATION_DECAY / len(neighbours)
```

A node divides what it passes among its neighbours rather than handing each a
full share. Without this, hubs win everything: the owner's own `person` node has
~80 edges, so it collects activation from every seed and re-broadcasts it, and a
query about a ticket comes back as a list of people. Dividing by degree keeps a
hub's individual links weak while a sparse, specific link stays strong.

Paths carrying less than 0.005 are pruned, which is what keeps the sweep bounded
regardless of graph size.

### 4. Scoring

```python
score = activation * (0.55 + 0.45 * strength)
if node was not a lexical hit:
    score /= 1 + log2(1 + degree)      # ubiquity penalty
```

Strength modulates but never dominates — a weak node with a strong lexical match
still ranks. The ubiquity penalty applies **only to associated nodes**: a node
linked to everything is structurally uninformative (it answers every query and
distinguishes none), but a node that earned its place by matching the cue keeps
its rank.

Filtered out by default: `capture` nodes (the daily digest covers them) and
`done`/`cancelled` nodes. Override with `--include-captures` / `--all`.

### 5. Budgeted payload

Results are trimmed to a `--budget` (default 1600 chars). Each node returns a
150-char excerpt *centred on the first matching term*, not its whole body — this
is where most of the token saving comes from. Each row reports `why`:
`"matched"` (lexical) or `"associated"` (reached by activation), so the caller
can tell recall from inference.

---

## Hebbian wiring — fire together, wire together

After each recall, the top hits are tallied pairwise in `coactivation`. An edge
is created only once a pair has co-activated **`HEBBIAN_THRESHOLD` (3) times**.

Waiting for repetition is the whole point: wiring on first co-occurrence would
let a single lexical coincidence — a ticket mentioning "export" briefly
co-activating the export-memory skill — become permanent structure.

Guard rails:

| Constant | Value | Why |
|---|---|---|
| `HEBBIAN_TOP` | 3 | only the top hits are eligible |
| `HEBBIAN_MAX_NEW` | 2 | max new edges per recall, so it never hairballs |
| `HEBBIAN_THRESHOLD` | 3 | co-activations required before an edge forms |
| `HEBBIAN_SKIP_TYPES` | skill, channel, person, capture | hubs, and things that should be linked by intent rather than correlation |

Disable per call with `--no-wire`. This is the one read path that writes; if that
matters for your use case, turn it off.

---

## Consolidation — the daily digest

`graph.py consolidate` runs once a day from the session-start hook. It digests
the day's raw `capture` nodes into durable notes and links, which is why recall
excludes captures by default: the digest is the better answer, and reading 200
raw prompt captures is not.

This is also the pressure valve on growth — captures are the fastest-growing node
type (~34% of all nodes). See
[growth projections](performance.md#growth-and-long-term-storage).

`graph.py maintain` handles the housekeeping pass; `suggest-links` proposes edges
for a human to accept rather than creating them.

---

## Context management — park and resume

Two commands exist purely to keep the *conversation* cheap, which is a different
budget from the database:

- **`park`** moves a chunk of context out of the window into a `parked` node,
  returning a handle.
- **`resume`** pulls back the thread you were on, plus anything parked.

The insight: the context window is the expensive thing, not the graph. Storing
more and carrying less is the trade this whole design makes.

---

## The genome — rules and skills as graph nodes

Conventions and learned skills are not files the model must read in full; they
are nodes, matched by scenario:

- `ingest-conventions` parses `CONVENTIONS.md` into `rule` nodes, one per
  section, tagged with scenario terms.
- `match-rules "<situation>"` returns only the rules that apply, inside a char
  budget, instead of the whole document.
- `add-skill` / `match-skills` / `use-skill` do the same for learned procedures,
  and `use-skill` bumps `usage_count` so a well-worn skill ranks higher.

Skill ranking is `fts_rank − min(usage_count, 6) × 0.5`: relevance is primary,
habit is a **bounded** booster, so a well-worn skill rises at most 3 places and a
weak match can never outrank a strong one.

Rule precedence is `user (100) > learned (50) > plugin (0)` — what you told it
beats what it inferred, which beats what it shipped with.

---

## Where to go next

- [Query methods](queries.md) — every read path and when to use which
- [Search patterns](search.md) — FTS5 syntax, cues, budgets
- [HTTP API](api.md) — the dashboard/server surface
- [Performance](performance.md) — measured latency, storage, growth, limits
- [neo4j backend](neo4j.md) — running a real graph database instead of SQLite
- [Contributing](../CONTRIBUTING.md)
