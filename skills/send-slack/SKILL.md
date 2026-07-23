---
name: send-slack
description: Send or schedule a Slack message — a DM to a person, a message to a channel, or a reply in a thread. Use for "message X on Slack", "post to #channel", "reply in that thread", "tell the team...", or when another skill (remind, delegate) needs to reach someone on Slack.
---

# send-slack

Reach someone on Slack using the connected Slack MCP tools.

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first.

## Steps

1. **Resolve the target.** If the person named someone, look them up:
   `mcp__claude_ai_Slack__slack_search_users` (people) or
   `slack_search_channels` (channels). If a `person` node already stores their
   Slack handle in props, use it.
2. **Draft in the person's voice.** If they have `writing-sample` nodes, apply
   the `writing-style` skill so the message sounds like them, not like a bot.
3. **Confirm before sending** (outward action): show the resolved recipient and
   the exact text, and get a yes — unless the person clearly already authorized
   this send. For scheduled sends, use `slack_schedule_message`.
4. **Send** via `mcp__claude_ai_Slack__slack_send_message` (or
   `slack_send_message_draft` if they want to review in Slack first).
5. **Remember it.** Optionally log a `note` node "Messaged <person> re <topic>"
   linked `mentions` the recipient, so the interaction is in memory.

## Notes
- Keep messages short and clear. Match the channel's formality.
- Never send to a channel you weren't asked to. When unsure of the target, ask.
