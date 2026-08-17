---
name: slack-inbox
description: Read the person's Slack (a DM, a channel, a thread) and act on it — this is the Slack-as-input entrypoint. Use for "check my Slack", "what did X send me", "handle my DMs", or when the person is driving memory-ledger from Slack. Reads recent messages, figures out the intent of each, and routes it to the right memory-ledger skill.
---

# slack-inbox

Slack is the person's inbox to memory-ledger. This skill reads messages and turns
each into the right action — so they can just talk, and the brain does the
routing.

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## Steps

1. **Read** the relevant Slack source with the Slack MCP tools:
   `slack_read_channel`, `slack_read_thread`, or search
   (`slack_search_public_and_private`). Default to the person's DM with
   themselves / the memory-ledger channel if they didn't specify.
2. **For each message, classify intent** and route to the matching skill:
   - a thing to do / deadline → **add-task** (auto micro-steps)
   - "remind me / alarm" → **remind**
   - a fact, decision, preference, person → **remember**
   - "what/when/who/status of..." → **recall**
   - "look up / google..." → **web-search**
   - "status of PROJ-123 / my tickets" → **read-jira**
   - "message / tell / reply to X" → **send-slack**
   - "ask X to..." → **delegate**
   If a message carries several intents, handle each.
3. **Always update memory** as a side effect (remember what matters).
4. **Reply in Slack** (via `send-slack`) with the focus contract: one clear
   next action per item, calm and short. If many messages, summarize the count
   and surface the single most important thing to do next.

## Notes
- Don't act on messages that are clearly not directed at the brain (chatter).
  When ambiguous, ask a one-line clarifying question rather than guessing.
- Confirm before any outward send on the person's behalf.
