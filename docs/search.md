# Search patterns

The brain has one index and several ways to interrogate it. Choosing well is the
difference between a 1,500-char answer and a 9,000-char one.

## The index

A single FTS5 virtual table over `title` + `body`:

```sql
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    title, body, content='nodes', content_rowid='rowid'
);
```

It is **contentless-synced**: the text lives in `nodes`, and three triggers
(`nodes_ai`, `nodes_ad`, `nodes_au`) keep the index aligned on insert, delete and
update. You never write to `nodes_fts` directly.

Consequences worth knowing:

- `props` is **not indexed**. Searching for an assignee or a Jira key only works
  if that text also appears in `title` or `body`. Filter on props with
  `list --type` / the dashboard chips instead.
- Deleting a node cascades to its edges (`ON DELETE CASCADE`) and to the FTS row.
- The index is ~21% of the database file. That is the cost of instant search.

## Term construction

Both read paths build prefix queries, but differently:

| Path | Query built | Effect |
|---|---|---|
| `search "a b"` | `"a"* "b"*` (implicit AND) | narrow — every term must appear |
| `recall "a b"` | `"a"* OR "b"*` | wide — any term seeds, ranking sorts it out |

Each term is quoted so punctuation and partial words are tolerated —
`OS-83101` survives, `ops_driver` survives.

`recall` additionally drops terms shorter than 3 characters and strips a bilingual
stop-list (English + pt-BR) before building the cue. `search` does not: it
searches exactly what you typed.

### Writing a good cue for `recall`

Because the terms are OR-ed and then reranked, **more specific nouns beat more
words**. Two or three distinctive terms outperform a sentence:

```bash
# good — distinctive, few
recall "ops_driver depot"
recall "OS-83101 region_name"

# worse — the stop-list eats most of it, the rest is generic
recall "what did we decide about the thing with the driver"
```

If a cue returns the owner's `person` node and a pile of channels, the cue was too
generic — the ubiquity penalty fights that, but it cannot invent specificity.

## Choosing a path

```
Do you know the exact string?           -> search
Do you want what's related to it too?   -> recall
Do you have the id?                     -> get-node / neighbors
Is it a date question?                  -> brain_query.py (agenda/week/...)
Is it "what must I act on"?             -> brain_query.py --mine
Is it "what's stuck"?                   -> open-loops
Do you need judgement about the answer? -> /brain (this one costs tokens)
```

## Budgets and drilling in

Every budgeted response tells you it is a summary:

```json
{"ok": true, "count": 6, "of": 15, "chars": 2813, "budget": 2600,
 "truncated": 4, "hint": "full body: get-node --id · everything: --full",
 "results": [...]}
```

- `count` vs `of` — how many you got vs how many matched.
- `truncated` — how many bodies were clipped.
- `hint` — the command that returns full text.

The correct follow-up to a truncated result is `get-node --id <id>`, not a
re-search with different words. Re-searching is what burns tokens.

## Excerpts

`recall` returns a 150-char window **centred on the first matching term**, not the
first 150 chars of the body. A match 800 characters into a note still returns
useful context. `search` trims from the start instead, sharing its budget evenly
across results (minimum 160 chars each).

## The `why` field

`recall` labels every row:

- `"matched"` — a lexical hit, it earned its rank
- `"associated"` — reached by spreading activation only

Treat `associated` rows as leads, not answers. They are the mechanism that
surfaces a blocker you did not ask about, and also the mechanism that occasionally
surfaces something irrelevant.

## Ranking recap

Full detail in [the algorithm](algorithm.md#recall--associative-retrieval):

```
seed        1 / (1 + fts_rank_position)
spread      × 0.45 per hop, ÷ degree (fan-out), 2 hops, convergent paths sum
strength    × (0.55 + 0.45 × strength)        strength = repetition, 21-day decay
ubiquity    ÷ (1 + log2(1 + degree))          associated nodes only
```

## Known sharp edges

- **A broad cue on a large graph is the slow path.** FTS `ORDER BY rank` ranks
  every match before `LIMIT 40` applies, so cost scales with matches, not
  results. See [performance](performance.md#scaling).
- **`recall` writes.** Hebbian wiring means a read can create up to 2 edges. Pass
  `--no-wire` for a pure read.
- **Stop-words are a fixed list**, not learned. A genuinely important word that
  happens to be on the list (`"how"` in "how-to") is dropped from the cue.
- **No stemming or synonyms.** `deploys` does not match `deploying`. Prefix
  matching covers some of this (`deploy*`), which is why terms are prefixed.
- **No vector search.** Retrieval is lexical + structural. A cue that shares no
  words with the target finds it only through edges.
