---
name: learn-skill
description: The neuroplasticity skill — teach the brain a new, reusable ability at runtime and remember WHEN to use it, so it can apply the learning to future situations in any project. Use when the person says "remember how to do this", "learn this workflow", "next time do it this way", "make this a skill", or when you notice a repeatable procedure worth keeping. Also the skill that RECALLS and REINFORCES learned skills.
---

# learn-skill

This is how the brain grows. A learned skill is a procedure written to
`~/.work_brain/learned_skills/<slug>/SKILL.md`, plus a `skill` node in the graph
whose triggers let the brain recognise when to use it again — including in a
different project than the one it was learned in.

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first.

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
   This writes the `SKILL.md` and the `skill` node.
5. **Connect it** into the graph so learning transfers: `add-edge` it
   `learned-from` the task/note that taught it, `applies-to` the relevant
   project/task type, `relates-to` sibling skills.

## B. RECALL before acting

At the **start** of handling a request, check whether the brain already knows how:
```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-skills "<the situation>"
```
If a skill matches, **read its `SKILL.md`** (`props.path` on the node) and follow
it instead of improvising. This is the whole point — reuse, don't rebuild.

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
