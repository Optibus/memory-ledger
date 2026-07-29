---
name: remember
description: Capture information into the second-brain memory graph — people, tasks, requirements, decisions, preferences, associations, context. Use whenever the person tells you something worth keeping, or when any other skill produces a fact worth persisting. This is the externalize-memory core; when in doubt, remember it.
---

# remember

Turn what the person just said into durable, linked memory.

Load only the conventions that apply — not the whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "store new information as linked nodes in the right area"`
(no model call). If it returns nothing, run `ingest-conventions` once first.
CONVENTIONS §2i explains the genome.

Then:

## Steps

1. **Extract the entities.** Pull out the distinct things mentioned: who
   (person), what (task/requirement/decision/note), which effort (project),
   and any preference or association. One node per real thing.
2. **Dedupe.** For each entity, `search "<title words>"` (and `list --type` if
   useful). If a close match exists, `update-node` / add to its body instead of
   creating a duplicate.
3. **Create nodes** with the right `--type` and a short, searchable `--title`.
   Put detail in `--body`. Put structured attributes in `--props` (slack, tz,
   url, jira_key, estimate_min, energy...). Set `--status`/`--due` on tasks.
4. **File it under its domain area** (CONVENTIONS §2e). Nothing durable floats
   loose: pass `--area` on creation so it lands in its domain in one call —
   `--area quote` for a quote, `--area health` for a symptom or energy note,
   `--area process` for one of Gabriel's ways of working. Run `areas` if you are
   unsure what exists; if the domain has no area yet and this is the second item
   of its kind, `add-area` first. A bad area name errors instead of creating an
   orphan, so it is safe to try.
5. **Link them.** Add edges that capture the real relationship (`assigned-to`,
   `part-of`, `about`, `depends-on`, `relates-to`, `mentions`...). The graph's
   value is the links — be generous but accurate. Cross-domain links are the
   valuable ones: a quote linked to the decision it justifies, a health episode
   linked to the day it cost.
6. **Confirm in one line.** Tell the person what you saved and how it's linked,
   e.g. `Saved "Ship weekly report" (due Thu 9am), linked to project Weekly
   Reports and to you.` Do not dump the JSON.

## Notes
- Preferences about how they like to work → a `person` node body or an
  `association`/`note` linked `about` them. These shape future replies.
- If they state a hard fact about themselves worth persisting across sessions,
  also consider whether it belongs in your own long-term memory.
- If nothing is worth keeping, say so briefly and don't create noise.
