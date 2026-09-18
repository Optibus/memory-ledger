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

3. **Rank — deadline first, then switch cost.** Order by hard deadline (overdue
   first), then `props.priority`. Deadlines are non-negotiable; everything with
   slack gets ordered by what it costs to *enter*, not by what it costs to do.

4. **Batch by context, then time-block.** Group the slack items that share a
   context — same repo, same ticket family, same kind of thinking (code / review
   / writing / comms) — and lay each **group** into a free window whole. Never
   interleave two contexts inside one window.
   - **Protect one deep block.** The longest free window goes to the group that
     needs a loaded mental model, uninterrupted. Don't fill it with small items
     just because they fit.
   - **Collect the small stuff into one slot.** Reviews, replies, quick asks and
     admin go in a single batch, not sprinkled between the real blocks.
   - Protect pre-meeting prep, and put low-context work in the short gaps
     between meetings — a 20-minute gap can't hold a deep block, so don't try.
   - Count the switches out loud in the plan. Two plans that fit the same tasks
     are not equally cheap; prefer the one with fewer re-entries.
   - Be realistic — don't overfill; call out anything that won't fit today.

5. **Reply in the focus contract.** Show the calendar spine, the blocked plan
   (with the switch count), and **one clear first action**. Calm and scannable —
   never a wall of text.

6. **Make it stick (gated — ask first).** Offer to:
   - set due dates on the picked tasks
     (`graph.py update-node --id <id> --due <ISO>`), and/or
   - drop calendar blocks (Google Calendar MCP `create_event`).
   Do it only on a yes.

## Notes
- No free windows? Say so and surface only the single most important item.
- **Why switch cost is a ranking input, not a preference.** The cheapest day is
  the one with the fewest re-entries, not the fullest one — stopping one mental
  model and loading another is the expensive event, and doing the work is not.
  `match-rules` in step 0 returns the rules that say so; follow them.
- A switch you accept mid-day gets a `graph.py park` first (where I was, next
  step, open question) so re-entry is a read. Re-plans start from `resume`.
- An undated task you pull into today should get today's due (with the user's ok)
  so it shows on the dashboard. You can also set/clear due dates straight from the
  dashboard (click the date chip).
- This is `plan-my-day` scoped to *today* — reuse that learned skill if present
  (`graph.py match-skills "plan my day"`).
