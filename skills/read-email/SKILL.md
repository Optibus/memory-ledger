---
name: read-email
description: Read and triage email (Gmail) — "check my email", "what did X email me", "any urgent emails", "summarize my inbox", "did the reply come in". Use to pull messages, summarize them without overwhelm, extract any action into a task, and (gated) draft replies in the person's voice. Reading is safe; sending is confirmed first.
---

# read-email

Make the inbox manageable. Read it, surface only what needs the person, and turn
obligations into tracked tasks, so email stops being a pile of things nobody
remembers.

## When to use

"Check my email", "what did X send", "anything urgent", "did the reply come in".

## Given

- **The Gmail MCP tools** (`mcp__claude_ai_Gmail__*`). If not authenticated, the
  authenticate flow runs first and the person is told what they are approving.
- **The scope asked about**: unread, a sender, a subject, a time window.
- **Unread state is Gabriel's own queue**, not a display detail. Treat it as data
  you may read and may not change.

## Rules honored

Ask the genome for the rules that apply before touching the mailbox (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "read and triage email, draft a reply"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne`.

Rule classes this skill touches, resolved from the person's own genome:

- **mailbox-state** (§5.2), via `match-rules "change the state of an email"`.
- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `ingest-context` when a message carries durable
  context, `add-task` when it implies a to-do, `writing-style` when a reply is
  drafted.
- **Reads:** Gmail, and `email` nodes for anything already tracked.
- **Writes:** none directly. Nodes are written by the skills this hands to.

## Deliverables

- **One email leads.** The reply opens with the single message needing action or a
  reply, then says how many others there are and offers to go through them.
- **Time-sensitivity is flagged**, not left for the person to notice.
- **Obligations became tasks.** Anything implying a to-do went through `add-task`,
  with the email preserved as a linked node so the context survives.
- **A drafted reply sounds like the person**, in the recipient's language, and was
  shown before anything was sent.
- **The mailbox is unchanged.** Same read and unread state, same labels, same
  location, unless the person was asked and agreed.

## Judged against

- **The unread count is what it was**, unless a yes is in the exchange.
- **A reader can act on the first line.** If the reply summarizes every message,
  the triage clause failed.
- **Nothing was sent without an explicit yes.**

## Guardrails — must not

- **Must not mark read, archive, move or relabel anything without asking.**
  Gabriel works from unread state, and clearing it silently destroys his queue.
- **Must not send a reply without an explicit yes.** It goes out under his name.
- **Must not summarize the whole inbox.** That is the anxiety this skill exists to
  reduce, restated as a wall of text.

## Reply (focus contract)

The one that needs him, bolded, then the count.

> **Alessandro is waiting on your acceptance test for OS-82316**, sent Tuesday.
> 11 other unread, nothing else time-sensitive. Want to go through them?
