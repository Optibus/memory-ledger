---
name: read-email
description: Read and triage email (Gmail) — "check my email", "what did X email me", "any urgent emails", "summarize my inbox", "did the reply come in". Use to pull messages, summarize them without overwhelm, extract any action into a task, and (gated) draft replies in the person's voice. Reading is safe; sending is confirmed first.
---

# read-email

Make the inbox manageable: read it, surface only what needs the person, and
turn action items into tracked tasks — so email stops being a pile of
unremembered obligations.

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first.

## Steps

1. **Connect.** Use the Gmail MCP tools (`mcp__claude_ai_Gmail__*`). Run the
   authenticate flow first if needed and tell the person what to approve.
2. **Read / search** the requested messages (unread, from a sender, a subject,
   a time window).
3. **Triage in the focus contract.** Don't summarize every email. Lead with the
   **one that needs action or a reply**, then say how many others there are and
   offer to go through them. Flag anything time-sensitive.
4. **Capture actions.** For emails that imply a to-do, use `add-task` (with its
   micro-step breakdown) and link the email as a `resource`/`note` node so the
   context is preserved.
5. **Replies are gated.** If asked to reply, draft using the `writing-style`
   skill in the recipient's language, show the draft, and only send after an
   explicit yes.

## Notes
- Never mark-read, archive, or send without confirmation.
- Keep summaries short and calm; the goal is less inbox anxiety, not more.
