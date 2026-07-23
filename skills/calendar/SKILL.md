---
name: calendar
description: Read and create calendar events (Google Calendar) — "what's on my calendar today", "am I free Thursday afternoon", "book 30 min to write the report", "schedule a call with X". Use for anything time-blocking or agenda-related. Can pull a task's due date from memory and block real time for it, and record events back into the graph.
---

# calendar

Turn intentions and deadlines into real blocks of time — externalizing the
"when" so it doesn't have to be tracked in the person's head.

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first.

## Steps

1. **Connect.** Use the Google Calendar MCP tools
   (`mcp__claude_ai_Google_Calendar__*`). If not yet authenticated, run the
   authenticate flow and tell the person what to approve.
2. **Read** the agenda / free-busy for the window asked about. Report it in the
   focus contract: the next/most important thing first, not a full dump.
3. **Create events** when asked to book time. Use the person's `tz`. For focus
   work, time-block by pulling the task from memory (`recall`) and creating an
   event titled after it; set a realistic duration from the task's
   `estimate_min` prop.
4. **Confirm before creating** (outward action): show title, date/time, and
   duration; get a yes unless already authorized.
5. **Remember it.** Optionally add a `meeting` node linked `relates-to` the
   task/project, so the calendar and the graph stay in sync.

## Notes
- For pure alarms/nudges (not agenda blocks) prefer the `remind` skill.
- Respect the person's language for event titles/descriptions.
- If Google Calendar isn't available, say so and offer `remind` as a fallback.
