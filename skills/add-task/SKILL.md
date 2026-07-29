---
name: add-task
description: Capture a task and immediately break it into tiny, low-activation-energy next-actions. Use when the person needs to do something, mentions a deliverable, or says "I have to...". Creates a task node, auto-generates micro-step subtasks, links it to the right project/person, and offers to set a reminder if it's time-bound.
---

# add-task

The anti-paralysis skill. A task you can't start is worse than no task — so the
job here is not just to record it, but to make **starting trivial**.

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## Steps

1. **Search** for an existing task first (avoid duplicates).
2. **Create the task node**: `add-node --type task --title "<verb + object>"`
   with `--status open`. If time-bound, set `--due` (ISO, using the person's
   `tz`). Estimate effort and store `--props '{"estimate_min":N,"energy":"low|med|high"}'`.
3. **Break it into micro-steps.** Generate 2–6 subtasks, each a *single
   physical action* that takes only a few minutes and needs no decision to
   begin ("open X", "list the 3 sections", "paste last week's version"). For
   each: `add-node --type subtask --title "..." --status open`, then
   `add-edge --src <task> --dst <subtask> --rel has-subtask`.
4. **Link context**: `assigned-to` the person, `part-of` a project,
   `blocked-by` anything it depends on.
5. **Offer time support.** If it has a deadline, offer the `remind` skill to set
   a real alarm.

## Reply (focus contract)

Confirm the task in one line, then show **only the first micro-step** as the
next action — e.g.

> Got it — **Ship weekly report**, due Thu 9am. First step: **open last week's
> report and duplicate it.** (5 more steps saved; say "next" when done.)

Do not show the whole subtask list unless asked. One step at a time.
