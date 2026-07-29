---
description: Pick up today's tasks and plan them — pull only YOUR actionable items (token-free), read the calendar spine, and lay them into your free windows. Ends with one clear first action.
---

# plan-today

Pick up the tasks you need to act on today and turn them into a realistic,
time-blocked plan. Reads are **token-free** (`brain_query`); planning uses the
calendar as the fixed spine.

Load only the conventions that apply — not the whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "plan the day, surface open loops and due tasks"`
(no model call). If it returns nothing, run `ingest-conventions` once first.
CONVENTIONS §2i explains the genome.

## Steps

1. **Pull today's work — token-free.** Run and read the output directly:
   - `python3 "$CLAUDE_PLUGIN_ROOT/scripts/brain_query.py" agenda --mine`
     — overdue + today + tomorrow, only *your* actionable tasks (the `--mine`
     filter already drops others' / delegated / waiting/blocked tasks).
   - `python3 "$CLAUDE_PLUGIN_ROOT/scripts/brain_query.py" open --mine`
     — the rest of your open backlog, to pull a few into today if there's room.
   Don't spend tokens re-deriving this.

2. **Read the calendar spine.** Fetch today's events (Google Calendar MCP
   `list_events`, local tz). Meetings, OOO and breaks are fixed — find the free
   work windows between them.

3. **Rank + time-block.** Order by hard deadline (overdue first), then
   `props.priority`. Fit tasks into the free windows, protecting pre-meeting prep.
   Be realistic — don't overfill; call out anything that won't fit today.

4. **Reply in the focus contract.** Show the calendar spine, the blocked plan,
   and **one clear first action**. Calm and scannable — never a wall of text.

5. **Make it stick (gated — ask first).** Offer to:
   - set due dates on the picked tasks
     (`graph.py update-node --id <id> --due <ISO>`), and/or
   - drop calendar blocks (Google Calendar MCP `create_event`).
   Do it only on a yes.

## Notes
- No free windows? Say so and surface only the single most important item.
- An undated task you pull into today should get today's due (with the user's ok)
  so it shows on the dashboard. You can also set/clear due dates straight from the
  dashboard (click the date chip).
- This is `plan-my-day` scoped to *today* — reuse that learned skill if present
  (`graph.py match-skills "plan my day"`).
