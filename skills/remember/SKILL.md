---
name: remember
description: Capture information into the second-brain memory graph — people, tasks, requirements, decisions, preferences, associations, context. Use whenever the person tells you something worth keeping, or when any other skill produces a fact worth persisting. This is the externalize-memory core; when in doubt, remember it.
---

# remember

Turn what the person just said into durable, linked memory. This is the
externalize-memory core, so when in doubt, remember it.

## When to use

Whenever the person tells you something worth keeping, and whenever another skill
produces a fact worth persisting.

## Given

- **What they said, in their words.** Memory is stored in the language it arrived
  in, never translated.
- **The existing graph**, to find the node this belongs on instead of creating a
  near-duplicate beside it.
- **The domain areas** (`areas`), which is where a durable thing gets filed. A bad
  area name errors instead of creating an orphan, so trying one is safe.

## Rules honored

Ask the genome for the rules that apply before storing (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "store new information as linked nodes in the right area"
```

Accountable for: `rule_s0_always-on-capture-the-offload-rule-non-n` (non-negotiable),
`rule_s2e_areas-one-hub-per-domain`,
.

Rule classes this skill touches, resolved from the person's own genome:

- **durable-storage** (§5.2), via `match-rules "store a durable fact"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `add-task` when what was said is actually work,
  `learn-skill` when it is actually a repeatable procedure.
- **Reads:** any node type, to dedupe before creating, plus `area` to file it,
  through `search`, `list` and `areas`.
- **Writes:** any node type, plus the edges `assigned-to`, `part-of`, `about`,
  `depends-on`, `relates-to`, `mentions`.

## Deliverables

- **One node per real thing.** The distinct entities were separated: who, what,
  which effort, and any preference or association.
- **Nothing was duplicated.** Each entity was searched first, and a close match was
  updated or extended instead of forked.
- **Titles are searchable, detail is in the body, attributes are in props.** Short
  title, detail in `--body`, structured values such as `slack`, `tz`, `url`,
  `jira_key`, `estimate_min`, `energy` in `--props`.
- **Nothing durable floats loose.** Every durable node was filed under its domain
  area with `--area` on creation. If the domain has no area yet and this is the
  second item of its kind, the area was created first.
- **The links are the value.** Edges capture the real relationship, generously but
  accurately. Cross-domain links are the valuable ones: a quote linked to the
  decision it justifies, a health episode linked to the day it cost.
- **The turn was not re-stored.** The capture hooks already saved the prompt, an
  answer summary and obvious to-dos, for free. Only the semantic nodes the
  heuristics cannot infer are added on top.

## Judged against

- **`search` on the person's own words finds it.** Memory that does not come back
  on recall was not stored, it was discarded slowly.
- **The node has at least one edge.** An unlinked node is a note in a drawer.
- **It appears under an area.** An orphan means the filing clause was skipped.
- **Its language matches the language it was said in.**

## Guardrails — must not

- **Must not create a near-duplicate of an existing node.** Two nodes for one
  thing split its history and neither half stays true.
- **Must not translate the person's words when saving.** Search is character
  based, so the original language stays findable.
- **Must not re-store what the hooks already captured.** That spends tokens on
  what was free.
- **Must not create noise.** If nothing is worth keeping, say so briefly.
- **Must not write a rule or durable fact into a local memory file.** Rules and
  durable facts live in the graph, which is the same memory from every machine and
  every working directory.

## Reply (focus contract)

One line: what was saved and how it is linked. Never the JSON.

> Saved **"Ship weekly report"**, due Thu 9am, linked to project Weekly Reports
> and to you.
