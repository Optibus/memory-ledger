---
name: slack-inbox
description: Read the person's Slack (a DM, a channel, a thread) and act on it — this is the Slack-as-input entrypoint. Use for "check my Slack", "what did X send me", "handle my DMs", or when the person is driving memory-ledger from Slack. Reads recent messages, figures out the intent of each, and routes it to the right memory-ledger skill.
---

# slack-inbox

Slack is the person's inbox to memory-ledger. This skill reads messages and turns
each into the right action, so they can just talk and the brain does the routing.

## When to use

"Check my Slack", "what did X send me", "handle my DMs", or any moment the person
is driving the brain from Slack rather than the terminal.

## Given

- **The Slack source**: `slack_read_channel`, `slack_read_thread`, or
  `slack_search_public_and_private`. With nothing specified, default to the
  person's DM with themselves or the memory-ledger channel.
- **The intent of each message**, which is what determines where it goes:
  a thing to do or a deadline to `add-task`, an alarm to `remind`, a fact or
  decision or person to `remember`, a question about state to `recall`, a lookup
  to `web-search`, a ticket to `read-jira`, an outbound message to `send-slack`,
  an ask for someone else to `delegate`. A message can carry several.

## Rules honored

Ask the genome for the rules that apply before acting on a message (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "read slack messages and route each to a skill"
```

Accountable for: `rule_s0_always-on-capture-the-offload-rule-non-n` (non-negotiable),
`rule_s1_how-to-respond-the-focus-contract-non-ne`.

Rule classes this skill touches, resolved from the person's own genome:

- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** every routing target above, plus `ingest-context` when
  a thread carries durable context worth extracting.
- **Reads:** Slack, and `message` nodes for anything already tracked.
- **Writes:** none directly. The routed skills write.

## Deliverables

- **Every message directed at the brain got routed.** Each intent reached the
  skill that handles it, and a message carrying several intents was handled for
  each one.
- **Chatter was left alone.** Messages clearly not directed at the brain produced
  no action.
- **Ambiguity became a question, not a guess.** One short clarifying line rather
  than an assumed intent.
- **Memory moved as a side effect.** Whatever mattered was remembered, since this
  is a capture surface as much as a routing one.
- **The Slack reply carries the focus contract.** One clear next action per item,
  calm and short. With many messages, the count plus the single most important
  thing to do next.

## Judged against

- **Every actioned message has a node or a state change behind it.** A message
  acknowledged but not recorded will be read again tomorrow.
- **No action was taken on chatter.**
- **The reply names one next action**, not a per-message digest.

## Guardrails — must not

- **Must not act on a message that was not directed at the brain.** Turning
  someone's aside into a task is how a brain becomes noise.
- **Must not guess at an ambiguous intent.** Ask in one line.
- **Must not send anything outward on the person's behalf without confirmation.**

## Reply (focus contract)

The count, then the single most important thing to do next.

> 6 new messages, 4 were chatter. **Ryllari is asking whether the export fix ships
> this sprint.** Want me to draft the answer?
