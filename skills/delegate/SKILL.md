---
name: delegate
description: Hand a task off — to a person (via Slack) or to an AI agent — and track it so it doesn't fall through the cracks. Use for "ask X to do this", "delegate this to...", "get someone on this", or when a task is better done by someone/something else. Creates the handoff, records who owns it, and sets a follow-up.
---

# delegate

Move work off the person's plate without losing sight of it. Delegation without
tracking is just forgetting, so the owner and the check-back are part of the
hand-off, not an afterthought.

## When to use

"Ask X to do this", "delegate this", "get someone on it", or any moment the work
is better done by someone or something else.

## Given

- **The task**, which may not exist in the graph yet.
- **The intended owner**: a `person` node carrying their Slack handle and
  `props.lang`, or the specialist agent that fits the work.
- **Why it matters and by when**, because an ask without either is the ask that
  gets ignored.

## Rules honored

Ask the genome for the rules that apply before handing anything off (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "delegate a task to a person and track the handoff"
```

Accountable for: `rule_s2c_state-awareness-open-loops`. A delegated task is still
an open loop.

Rule classes this skill touches, resolved from the person's own genome:

- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (ordered):** `add-task`, when the work is not in the graph yet. The
  ownership edge needs a node to hang off.
- **Optional (enriching):** `send-slack` to reach a person, `remind` to set the
  check-back.
- **Reads:** `person` nodes (Slack handle, `lang`), the `task` being moved.
- **Writes:** `task`, re-pointed with `assigned-to` and stamped with
  `props.state` and `waiting_on`.

## Deliverables

- **Ownership moved, visibly.** The task is `assigned-to` the new owner, and the
  previous ownership edge is removed rather than left to contradict it.
- **The ask reached them.** A message went out in the owner's language saying what
  the work is, why it matters, and by when.
- **The loop stayed open on the person's side.** The task carries a state showing
  it is waiting on someone, so `follow-up` and `recall` both surface it as
  delegated rather than as done.
- **A check-back exists.** A reminder is set for before the due date, not after it.

## Judged against

- **`open-loops` shows the task with `waiting_on` naming the owner.** If it
  vanished from the loops view, the hand-off lost it.
- **Exactly one `assigned-to` edge points out of the task.** Two owners means
  nobody owns it.
- **The check-back date precedes the due date.** A nudge after the deadline is a
  post-mortem, not a follow-up.

## Guardrails — must not

- **Must not send the ask without confirmation.** It goes out under the person's
  name, to a colleague.
- **Must not drop the task from tracking once it is someone else's.** Delegated is
  a state, not an ending.
- **Must not write the ask in English by default.** Read `props.lang` from the
  owner, and when it is unknown, ask and store it.

## Reply (focus contract)

One line: who owns it now, and when you will check back.

> Handed "Draft the deck" to Dana on Slack. I will nudge you to check in Wed.

**Delegating to an AI agent instead:** pick the specialist that fits, dispatch it
with a self-contained brief, and capture the result back as a `note` linked to the
task. The tracking clauses above apply unchanged, because an agent can drop work
too.
