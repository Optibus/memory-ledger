---
name: remind
description: Set a real reminder or alarm that fires on its own at a chosen time and pings the person on Slack — not just a "remember to..." note. Use for "remind me to...", "set an alarm for...", "nudge me at 3pm", "before the deadline". Beats losing track of time by moving the remembering out of the person's head and onto a schedule that runs even when Claude Code is closed.
---

# remind

Create a reminder that actually fires. Two parts: a reminder **node** in the
graph (so it's part of memory) and a scheduled **routine** (so it fires on its
own and DMs Slack).

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first.

## Steps

1. **Resolve the time.** Turn "tomorrow 9am" / "in 2 hours" / "every weekday
   8:45" into a concrete time using the person's `tz` (from their `person`
   node props). One-off → a datetime; recurring → a cron expression.
2. **Record it in memory:** `add-node --type reminder --title "<what>"
   --due <ISO> --status open --props '{"channel":"<slack target>","recurring":"<cron or empty>"}'`.
   Link it `about` whatever it concerns (a task, project, meeting) with
   `add-edge`.
3. **Schedule the real alarm.** Invoke the **`schedule` skill** (which uses the
   `CronCreate` tool under the hood) to create a routine that fires at the
   resolved time. The routine's prompt should be self-contained, e.g.:

   > second-brain reminder firing. Use the `send-slack` skill to DM
   > `<slack target>`: "⏰ <message>". Then run
   > `python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" update-node --id <reminder id> --status done`
   > (skip the status update if it is a recurring reminder).

   Give the routine a clear name like `sb-remind-<slug>`. For recurring
   reminders pass the cron expression; for one-offs schedule the single run.
4. **Confirm** in one line: what will fire, when (in their local time), and
   where it'll land. e.g. `Alarm set: I'll DM you "⏰ Standup" at 8:45 every
   weekday.`

## Notes
- If Slack isn't wired up, fall back to a `PushNotification`, and say so.
- Snooze / cancel = update the reminder node and update/delete the routine via
  the `schedule` skill (`CronList` / `CronDelete`).
- Keep reminder text short and kind — a nudge, not a nag.
