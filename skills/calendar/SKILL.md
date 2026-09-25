---
name: calendar
description: Read and create calendar events (Google Calendar) — "what's on my calendar today", "am I free Thursday afternoon", "book 30 min to write the report", "schedule a call with X". Use for anything time-blocking or agenda-related. Can pull a task's due date from memory and block real time for it, and record events back into the graph.
---

# calendar

Turn intentions and deadlines into real blocks of time, so the "when" lives on a
calendar instead of in the person's head.

## When to use

Anything agenda-shaped or time-blocking: what is on today, am I free Thursday,
book me 30 minutes for the report, schedule a call.

## Given

- **The window they asked about**, or the work they want time for.
- **The Google Calendar MCP tools** (`mcp__claude_ai_Google_Calendar__*`). If the
  connection is not authenticated yet, the authenticate flow runs first and the
  person is told what they are approving.
- **The person's timezone**, from their `person` node props. Every time resolves
  in it.
- **The task being blocked, when there is one.** Its `props.estimate_min` is the
  honest duration, not a round number you picked.

## Rules honored

Ask the genome for the rules that apply before reading or booking (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "read the calendar and block time for a task"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne` (the next
thing first, never a full agenda dump).

Rule classes this skill touches, resolved from the person's own genome:

- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `recall`, to find the task whose time is being blocked.
- **Reads:** Google Calendar, `task` props (`estimate_min`, `due`), and
  `person` for the timezone.
- **Writes:** a `meeting` node linked `relates-to` the task or project, when the
  event is worth remembering.

## Deliverables

- **The agenda is answered, not dumped.** The window is read and reported as the
  next or most important thing first.
- **Booked time is real time.** An event exists at a concrete time in the person's
  timezone, with a duration drawn from the task's estimate rather than invented.
- **Outward creation was confirmed.** Title, date, time and duration were shown
  and agreed before the event was created.
- **Calendar and graph agree.** An event worth remembering exists as a `meeting`
  node linked to the work it serves.

## Judged against

- **The event's timezone matches the person's**, so "9am" means their 9am.
- **The duration traces to something.** Either the task's `estimate_min` or a
  number the person gave.
- **Nothing was created without a yes**, visible in the exchange.

## Guardrails — must not

- **Must not create, move or delete an event without confirmation.** A calendar is
  shared with other people, so a silent write reaches them too.
- **Must not report the full agenda when one thing was asked about.** That is the
  overwhelm §1 exists to prevent.
- **Must not write event titles in English by default.** They are read by whoever
  is invited, so the recipient's language applies.

## Reply (focus contract)

The next commitment, bolded, then the count of what else is in the window.

> Next: **Team planning, 14:00, 45 min.** Three more things after it today.

For pure alarms and nudges rather than agenda blocks, hand to `remind`. If Google
Calendar is unavailable, say so and offer `remind` as the fallback.
