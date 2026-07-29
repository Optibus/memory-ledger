---
name: learn-skill
description: The neuroplasticity skill — teach the brain a new, reusable ability at runtime and remember WHEN to use it, so it can apply the learning to future situations in any project. Use when the person says "remember how to do this", "learn this workflow", "next time do it this way", "make this a skill", or when you notice a repeatable procedure worth keeping. Also the skill that RECALLS and REINFORCES learned skills.
---

# learn-skill

This is how the brain grows. A learned skill is a `skill` **node in the graph**
— its triggers and its full procedure live in the DB, so the brain can recognise
when to use it again in any project, on any machine, from the phone. **The DB is the source of truth — no
markdown file is written** (skills used to be lone `.md` files, which isolated
them to one machine; they're nodes now).

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## A. LEARN a new skill

Use when a procedure will recur (the person taught it, or you just did it and it
worked).

1. **Name it** — a short slug (`triage-sentry`, `end-of-day-review`) and a clear
   title.
2. **Capture the triggers** — the situations it fits, in the person's own words
   ("closing my day", "a Sentry alert comes in"). Fuzzy is fine; matching is
   associative.
3. **Write the procedure** — concrete, ordered steps someone (or you) can follow
   cold.
4. **Create it:**
   ```
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" add-skill \
     --name <slug> --title "<title>" \
     --when "<trigger situations>" \
     --body "<numbered steps>"
   ```
   This stores the skill as a **node in the brain** (DB-first, synced
   everywhere). Add `--with-file` only if you also want a throwaway local
   `SKILL.md` export — you rarely do.
5. **Connect it** into the graph so learning transfers: `add-edge` it
   `learned-from` the task/note that taught it, `applies-to` the relevant
   project/task type, `relates-to` sibling skills.

## B. RECALL before acting

At the **start** of handling a request, check whether the brain already knows how:
```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-skills "<the situation>"
```
Matching ranks by **relevance first**, with reinforcement (usage) as a bounded
booster. If a skill matches, **read its procedure from the node**
(`get-node --id <skill-id>` — the steps are in the node body) and follow it
instead of improvising. This is the whole point — reuse, don't rebuild.

> **Bring existing markdown skills into the brain:** run
> `graph.py ingest-skills` once — it scans the plugin `skills/` + any
> `learned_skills/` and upserts each as a node (idempotent; re-run to refresh).

## C. REINFORCE after it fires

When a learned skill was actually used and helped:
```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" use-skill --id <skill-id>
```
Strengthens the pathway (usage↑, confidence↑) so proven skills surface first
next time. If a skill misfired or is stale, tell the person and consider editing
its triggers (`add-skill` again with the same `--name` updates it) or lowering
its relevance.

## Reply (focus contract)
One line: what was learned and when it'll fire. e.g.
`Learned **end-of-day-review** — I'll reach for it whenever you're wrapping up
the day. It'll get sharper each time we use it.`

## Notes
- Keep skills small and single-purpose; compose them rather than writing megaskills.
- Learned skills are shared across every project (they live in the global brain),
  so write triggers/steps to be project-agnostic where you can.
