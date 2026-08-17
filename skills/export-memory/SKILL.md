---
name: export-memory
description: Export the entire memory-ledger memory graph to portable files — human-readable Markdown (with wikilinks) and/or JSON (nodes + edges). Use for "export my memory", "back up my brain", "give me my notes as files", "dump the graph". The whole memory is one SQLite file, so it's already portable; this makes it readable and shareable.
---

# export-memory

Turn the graph into files the person can read, back up, move to another machine,
or open in a notes app.

## When to use

"Export my memory", "back up my brain", "give me my notes as files", "dump the
graph".

## Given

- **The format**, either stated or inferred: `md` for readable Obsidian-style
  wikilinks, `json` for structured nodes and edges, `both` by default.
- **Where the brain lives.** `~/.second_brain/graph.db` unless `$SECOND_BRAIN_DB`
  or `$SECOND_BRAIN_HOME` says otherwise. The default export directory is
  `~/.second_brain/exports/<timestamp>/`.

## Rules honored

Ask the genome for the rules that apply before exporting (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "export the memory graph to files"
```

Accountable for: `rule_s2d_storage-one-local-file-an-optional-mirro` (the DB is
the source of truth, an export is a copy).

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Reads:** every node type in the graph, through `export`.
- **Writes:** none. Export produces files and changes no node.

## Deliverables

- **The files exist and are named.** The export ran, and the reply gives the
  directory plus the node and edge counts, so the person can tell a full export
  from a truncated one.
- **The Markdown re-opens as a graph.** Ids appear as `[[node_id]]` wikilinks, so
  the export is still connected in a tool like Obsidian.
- **The cheaper path is offered.** The person is told that copying `graph.db`
  moves the entire brain, since that is what they usually want when they say
  "back up".

## Judged against

- **The counts in the reply match the graph.** A silent partial export is the
  failure that matters here, and only the counts reveal it.
- **The graph is unchanged.** An export that mutated a node is a bug, not an export.

## Guardrails — must not

- **Must not treat the export as the source of truth.** Editing exported files
  changes nothing. The DB is the brain.
- **Must not write the export inside a repo.** The brain is global and private,
  and an export under a working directory gets committed by accident.

## Reply (focus contract)

One line: where it went and how much went with it.

> Exported **312 nodes, 806 edges** to `~/.second_brain/exports/2026-09-18/`.
> Want the raw `graph.db` too? Copying that one file moves the whole brain.
