---
name: add-task
description: Capture a task and immediately break it into tiny, low-activation-energy next-actions. Use when the person needs to do something, mentions a deliverable, or says "I have to...". Creates a task node, auto-generates micro-step subtasks, links it to the right project/person, and offers to set a reminder if it's time-bound.
---

# add-task

The anti-paralysis skill. A task the person cannot start is worse than no task, so
the accountability here is not recording the work. It is making **starting
trivial**.

## When to use

The person says they have to do something, names a deliverable, or a deadline
lands on them. Also whenever another skill turns an inbound ask into work that is
now theirs.

## Given

- **The request**, in whatever words they used. The task title is a verb plus an
  object, drawn from that, not from your paraphrase.
- **The graph**, to check whether this task already exists and which project,
  person or area it belongs under.
- **The person's timezone**, from their `person` node props. Every due date is
  resolved in it.

## Rules honored

Ask the genome for the rules that apply before writing anything (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "capture a task and break it into micro steps"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne` (one next
action, never the whole list), `rule_s2e_areas-one-hub-per-domain` (nothing
durable floats loose).

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `remind` when the task is time-bound and a real alarm
  would help more than a due date.
- **Reads:** `task` to dedupe, `person` for the timezone and owner, `project`
  and `area` for filing, through `search`, `list` and `areas`.
- **Writes:** `task`, `subtask`, and the edges `has-subtask`, `assigned-to`,
  `part-of`, `blocked-by`.

## Deliverables

- **The task exists once.** A `task` node carries the work, with `status open`,
  and no near-duplicate of an existing task was created.
- **Starting is trivial.** Between 2 and 6 `subtask` nodes hang off it, each a
  single physical action that takes minutes and needs no decision to begin. "Open
  last week's report" qualifies. "Draft the report" does not.
- **Effort is estimated.** `props.estimate_min` and `props.energy` are set, so
  `calendar` can block real time for it later.
- **It is connected.** The task is linked to whoever owns it, whatever project it
  belongs to, and anything blocking it.
- **Time-bound work is offered an alarm.** If a deadline exists, `remind` is
  offered rather than assumed.

## Judged against

A reader who never saw the exchange can confirm, from the graph alone:

- **The first subtask is startable cold.** If they would have to decide something
  before beginning it, the breakdown failed.
- **The due date is in the person's timezone**, not UTC and not the model's guess.
- **The task hangs off something.** An orphan task means the linking clause was skipped.

## Guardrails — must not

- **Must not show the whole subtask list in the reply.** Seeing six steps is the
  overwhelm this skill exists to prevent.
- **Must not create a second task for work already tracked.** Duplicate tasks
  split the state of one real obligation and neither half stays true.
- **Must not invent a due date.** An assumed deadline becomes real pressure the
  person never agreed to. If it is time-bound and the date is unknown, ask.

## Reply (focus contract)

Confirm the task in one line, then show **only the first micro-step**.

> Got it, **Ship weekly report**, due Thu 9am. First step: **open last week's
> report and duplicate it.** (5 more steps saved, say "next" when done.)
