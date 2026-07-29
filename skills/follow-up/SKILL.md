---
name: follow-up
description: Surface every open loop and drive it toward closed. Use for "what's still open", "what do I owe", "follow up on my stuff", "what needs closing", "am I blocking anyone". Tracks open tasks, PRs in flight, emails needing a reply, and messages to answer — each holds its state until something changes it — and nudges the person to close the oldest/highest-priority first.
---

# follow-up

The brain is **state-aware**: every trackable thing holds its state until
something changes it. This skill reads that state, shows what's still open, and
pushes each loop toward done.

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## 1. Read the state
```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" open-loops [--stale-days N]
```
Returns everything still open (tasks/subtasks not done, tracked PRs/emails/
messages, anything with a non-closed `props.state`), oldest-first with
`age_days`, `overdue`, `stale`, and `waiting_on`, plus counts by kind/priority.

## 2. Refresh what may have changed
State is only true until reality moves — so before nudging, re-check the live
sources for the loops that depend on others:
- **PRs** — `gh pr view` for review decision + CI. If merged/approved/green → advance the state.
- **Emails / Slack messages needing a reply** — did they reply? did you? re-read the thread.
- **Jira** — status transitions (via `read-jira` / ingest-context).
Then move the loop with **`set-state`** (this stamps *when* it changed, so age is honest):
```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" set-state --id <id> --state <open|doing|blocked|waiting|needs-reply|answered|done|merged|closed> [--waiting-on "<who>"]
```
Complete finished tasks with `complete` (archives them). Nothing closes itself.

## 3. Track new open loops
When something arrives that needs a future action, make it a stateful node so it
can't be forgotten:
- a **PR** to move → `add-node --type pr` with `props {state, pr_url, review}`
- an **email** needing a reply → `add-node --type email` `props {state:"needs-reply", from, url}`
- a **message** to answer → `add-node --type message` `props {state:"needs-reply", channel}`
Link them (`about`/`relates-to`/`assigned-to`). `ingest-context`, `read-email`,
`slack-inbox`, and `read-jira` should create these as they detect them.

## 3b. Stalled hand-offs → check in with the owner
When a loop has been **in someone else's hands for a while** — a node whose
`props.waiting_on` points at another person (or a task reassigned `assigned-to`
someone else) and whose `age_days >= --stale-days` with no answer — **follow up
with that person**. The goal is supportive, not chasing: understand if they're
**blocked, need help, or why it hasn't moved yet**.

1. Resolve the person (their `person` node → Slack id + `props.lang`).
2. Draft a **direct, friendly** check-in **in that person's language** (see
   CONVENTIONS §1 — pt-BR for most of CORD, en for Ravid; if the language is
   unknown, ask the user and store it on the person node). Keep it short and
   warm — e.g. *"oi X, tudo bem? só passando pra saber como tá o <coisa> — tá
   travado em algo ou precisa de uma mão?"*
3. Confirm before sending (outward action), then send via the `send-slack` skill.
4. Record it: stamp the loop `set-state --waiting-on "<person> (pinged <date>)"`
   and, if useful, set a `remind` to check back.

## 4. Nudge (focus contract)
Lead with the **single most important loop to close now** (overdue → highest
priority → oldest). Then a short grouped list — cap it, don't dump all 28.
Call out anything **you're blocking someone else on** (a `waiting_on` pointing
back at the person, or a review others need). Offer to take the next action
(reply, tick, request review) rather than just listing.

## Notes
- This is also what the **SessionStart hook** surfaces automatically each session
  (a short version) — this skill is the full, actionable pass.
- "Close as soon as possible" is the goal: every loop should be trending toward a
  closed state; a loop that hasn't moved in `--stale-days` is flagged.
