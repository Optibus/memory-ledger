---
name: delegate
description: Hand a task off — to a person (via Slack) or to an AI agent — and track it so it doesn't fall through the cracks. Use for "ask X to do this", "delegate this to...", "get someone on this", or when a task is better done by someone/something else. Creates the handoff, records who owns it, and sets a follow-up.
---

# delegate

Move a task off the person's plate without losing sight of it. Delegation
without tracking is just forgetting — so we always record the owner and a
follow-up.

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first.

## Two kinds of delegation

**To a person:**
1. Ensure the task exists in the graph (`add-task` if not).
2. Re-point ownership: `add-edge --src <task> --dst <person> --rel assigned-to`
   (and remove the old assignee edge if it moved off the person).
3. Reach them via the **`send-slack`** skill with a clear, kind ask: what,
   why it matters, and by when. Confirm before sending.
4. Set a **follow-up** via the `remind` skill (e.g. day before the due date:
   "check in with <person> on <task>").
5. Mark the task `--status blocked` or add a `props` note that it's delegated,
   so `recall` shows it as "waiting on <person>".

**To an AI agent:**
1. Choose the right specialist (e.g. an `ai-delegator` skill/agent or a
   `general-purpose` agent) for the work.
2. Dispatch it with a self-contained prompt. Capture the result back into
   memory as a `note` linked to the task.

## Reply (focus contract)

One line: who now owns it, and when you'll check back. e.g.
`Handed "Draft the deck" to Dana on Slack. I'll nudge you to check in Wed.`
