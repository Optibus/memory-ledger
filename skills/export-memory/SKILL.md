---
name: export-memory
description: Export the entire memory-ledger memory graph to portable files — human-readable Markdown (with wikilinks) and/or JSON (nodes + edges). Use for "export my memory", "back up my brain", "give me my notes as files", "dump the graph". The whole memory is one SQLite file, so it's already portable; this makes it readable and shareable.
---

# export-memory

Turn the graph into files the person can read, back up, move to another
machine, or open in a notes app.

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## Steps

1. Ask (or infer) the format: `md` (readable, Obsidian-style wikilinks),
   `json` (structured nodes/edges), or `both` (default).
2. Run:
   ```
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" export --format both [--out <dir>]
   ```
   Default output dir is `memory/exports/<timestamp>/`.
3. Report where it went and the counts (nodes/edges), in one line. Offer to
   also point them at the raw DB file (single portable `graph.db`) if they want
   the whole thing to copy elsewhere.

## Notes
- The SQLite file *is* the source of truth and is itself portable — copying
  `memory/graph.db` moves the entire brain. Export is for readability/sharing.
- Markdown export ids appear as `[[node_id]]` links, so the export re-opens as
  a connected graph in tools like Obsidian.
