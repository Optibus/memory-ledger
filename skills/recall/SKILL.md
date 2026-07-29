---
name: recall
description: Retrieve and resurface things from the second-brain memory graph — answer "what do I know about X", "what's on my plate", "what's due", "what was decided about Y", "how is this connected". Use whenever the person asks about past context, or before acting so you don't lose what's already known.
---

# recall

Get context back out of the graph, and surface it without overwhelming.

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## Pick the right lookup

- **Topic / "what do I know about X"** → `search "X"`, then `get-node --id` or
  `neighbors --id X --depth 1` on the best hit to pull its connections.
- **"What's on my plate" / open work** → `list --type task --status open`
  (and `--status doing`).
- **"What's due / am I behind"** → `due --within-days 7`. Flag `overdue` first.
- **"How is X connected to Y" / show the web** → `neighbors --id X --depth 2`.
- **A specific person/project** → `search` for it, then `neighbors` to see
  everything hanging off it.

## Present it (focus contract)

- Lead with **the single most relevant thing**, bolded.
- If there's more, say the count ("3 other tasks are open") and offer to show
  them — don't dump them all unprompted.
- Overdue/at-risk items come first, in plain calm language.
- Never paste raw JSON. Translate ids into titles.
