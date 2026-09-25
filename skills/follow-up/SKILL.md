---
name: follow-up
description: Surface every open loop and drive it toward closed. Use for "what's still open", "what do I owe", "follow up on my stuff", "what needs closing", "am I blocking anyone". Tracks open tasks, PRs in flight, emails needing a reply, and messages to answer — each holds its state until something changes it — and nudges the person to close the oldest/highest-priority first.
---

# follow-up

The brain is state-aware: every trackable thing holds its state until something
changes it. This skill reads that state, refreshes what reality has moved, and
pushes each loop toward closed.

## When to use

"What is still open", "what do I owe", "am I blocking anyone", "what needs
closing". Also the full version of what the SessionStart hook surfaces briefly
each session.

## Given

- **`open-loops [--stale-days N]`**, which returns everything still open, oldest
  first, with `age_days`, `overdue`, `stale` and `waiting_on`. An open loop is an
  unfinished `task` or `subtask`, a tracked `pr`, `email`, `message` or
  `reminder`, or any node whose `props.state` is not one of `done, cancelled,
  closed, answered, resolved, merged, shipped, deployed, declined`.
- **The live sources behind those loops**, because state is only true until
  reality moves: `gh pr view` for review and CI, the mail and Slack threads for
  replies, Jira for transitions.
- **The owner of a stalled loop**, from `props.waiting_on` or the `assigned-to`
  edge, with their `person` node giving the Slack handle and `props.lang`.

## Rules honored

Ask the genome for the rules that apply before nudging (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "surface open loops and follow up with the owner"
```

Accountable for: `rule_s2c_state-awareness-open-loops`,
`rule_s1_how-to-respond-the-focus-contract-non-ne`.

Rule classes this skill touches, resolved from the person's own genome:

- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `send-slack` to reach a stalled owner, `remind` to set
  a check-back, `complete-task` to close what finished.
- **Reads:** `task`, `subtask`, `pr`, `email`, `message`, `reminder`, through
  `open-loops`, plus the live sources behind them: GitHub, Gmail, Slack, Jira.
- **Writes:** `set-state` on any loop that moved, plus new `pr`, `email` and
  `message` nodes for loops that were not tracked yet.

## Deliverables

- **State reflects reality, not the last guess.** Before anything is reported,
  loops that depend on other people were re-checked against their live source, and
  each one that moved was advanced with `set-state`, which stamps when it changed
  so age stays honest.
- **New loops became tracked.** Anything that arrived needing a future action
  exists as a stateful node: a `pr` with `{state, pr_url, review}`, an `email`
  with `{state:"needs-reply", from, url}`, a `message` with
  `{state:"needs-reply", channel}`, each linked to what it concerns.
- **One loop leads.** The reply opens with the single loop to close now, chosen
  overdue first, then priority, then age.
- **What the person is blocking is called out.** A loop whose `waiting_on` points
  back at them, or a review others need, is named explicitly, because that cost
  falls on someone else.
- **Stalled hand-offs get a supportive check-in.** When a loop has sat in someone
  else's hands past `--stale-days`, a short, warm message goes to that person in
  their language, asking whether they are blocked or need a hand, not chasing
  them. After sending, the loop is stamped `--waiting-on "<person> (pinged
  <date>)"`.

## Judged against

- **Every reported loop's `state_since` is at least as recent as the last live
  check.** A loop reported without refreshing is a stale claim presented as fact.
- **The reply names one next action**, not 28 loops.
- **A check-in that went out is recorded on the loop.** An un-stamped ping will be
  sent again next week, which is how a supportive nudge becomes nagging.

## Guardrails — must not

- **Must not report a loop as open without re-checking its source.** The whole
  value here is that the state is true, and a wrong "still waiting" sends the
  person to chase something already done.
- **Must not dump the full list.** Cap it, lead with one.
- **Must not send a check-in without confirmation**, and never in English by
  default. Read `props.lang` from the person, and when it is unknown, ask and
  store it.
- **Must not let a loop close itself.** Nothing resolves silently, every state
  change is explicit.

## Reply (focus contract)

The single most important loop to close now, then a short grouped remainder, then
the offer to take the action rather than just list it.

> **Priya is waiting on your review of PR #7357**, 4 days. 6 other loops: 2
> overdue tasks, 3 emails, 1 PR. Want me to open the review?
