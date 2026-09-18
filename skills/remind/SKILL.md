---
name: remind
description: Set a real reminder or alarm that fires on its own at a chosen time and pings the person on Slack — not just a "remember to..." note. Use for "remind me to...", "set an alarm for...", "nudge me at 3pm", "before the deadline". Beats losing track of time by moving the remembering out of the person's head and onto a schedule that runs even when Claude Code is closed.
---

# remind

Create a reminder that actually fires. Two halves: a reminder **node** so it is
part of memory, and a scheduled **routine** so it fires on its own, even when
Claude Code is closed.

## When to use

"Remind me to", "set an alarm", "nudge me at 3pm", "before the deadline".

## Given

- **The time, in the person's words.** "Tomorrow 9am", "in 2 hours", "every
  weekday 8:45". Resolving it needs their `tz` from their `person` node props. A
  one-off resolves to a datetime, a recurring one to a cron expression.
- **What the reminder concerns**, so it can be linked to the task, project or
  meeting it belongs to.
- **Where the nudge lands**: their Slack target, or a `PushNotification` if Slack
  is not wired up.

## Rules honored

Ask the genome for the rules that apply before scheduling (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "set a reminder that fires on a schedule"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne` (time
support), `rule_s2c_state-awareness-open-loops` (a reminder is an open loop), and
on the nudge text itself.

Rule classes this skill touches, resolved from the person's own genome:

- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `send-slack`, which the fired routine uses to deliver
  the nudge.
- **Reads:** `person` (`tz`, Slack target), the routine store.
- **Writes:** a `reminder` node with `--due` and
  `props {channel, recurring}`, linked `about` whatever it concerns.

## Deliverables

- **The time is concrete and in their timezone.** No relative phrasing survives
  into the stored value.
- **It exists in memory.** A `reminder` node with status open, a due date, and a
  link to what it concerns, so `open-loops` and `recall` both see it.
- **A real alarm is scheduled.** A routine named `sb-remind-<slug>` fires at the
  resolved time, with a self-contained prompt that delivers the nudge and closes
  the reminder node afterward, skipping that close for a recurring one.
- **The person knows what will happen.** The confirmation says what fires, when in
  their local time, and where it lands.

## Judged against

- **The routine exists in `CronList`.** A reminder node with no routine behind it
  is a note pretending to be an alarm, which is exactly the failure this skill
  exists to prevent.
- **The stored time in their timezone matches what they asked for.**
- **A fired one-off reminder ends up `done`**, and a recurring one does not.

## Guardrails — must not

- **Must not create the node without the routine.** Memory of an alarm is not an
  alarm.
- **Must not resolve a time in UTC or the model's timezone.** An alarm at the
  wrong hour is worse than none, because it is trusted.
- **Must not nag.** The text is a short, kind nudge.

## Reply (focus contract)

One line: what fires, when, and where.

> Alarm set: I will DM you "Standup" at 8:45 every weekday.

Snooze or cancel by updating the reminder node and its routine through the
`schedule` skill (`CronList`, `CronDelete`).
