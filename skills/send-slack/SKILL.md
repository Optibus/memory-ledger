---
name: send-slack
description: Send or schedule a Slack message — a DM to a person, a message to a channel, or a reply in a thread. Use for "message X on Slack", "post to #channel", "reply in that thread", "tell the team...", or when another skill (remind, delegate) needs to reach someone on Slack.
---

# send-slack

Reach someone on Slack in the person's own voice, in the recipient's language, at
a register the recipient can act on.

## When to use

"Message X on Slack", "post to #channel", "reply in that thread", "tell the
team". Also whenever `remind`, `delegate` or `follow-up` needs to reach someone.

## Given

- **The target.** A `person` node's stored Slack handle when one exists, otherwise
  `slack_search_users` or `slack_search_channels`.
- **The recipient's language**, from their `person` node `props.lang`. A Slack
  message in the recipient's own language is expected, not English by default.
- **The recipient's role**, which sets the register. What a developer needs and
  what a product owner needs are different messages carrying the same finding.
- **The person's voice**, from their `writing-sample` nodes.

## Rules honored

Ask the genome for the rules that apply before drafting (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "draft a slack message to <recipient>"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne` (the language
and tone rules for writing to someone else).

Rule classes this skill touches, resolved from the person's own genome:

- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

Also run `match-skills "<recipient's role>"`, because an audience-specific
communication skill may be learned in this brain and is more precise than
anything this file can say.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (ordered):** `writing-style`, because the draft has to be in the
  person's voice before it is reviewed, not after.
- **Reads:** `person` props (`slack`, `lang`, role), `writing-sample`, `channel`,
  through `slack_search_users` and `slack_search_channels`.
- **Writes:** a `note` recording the interaction, linked `mentions` the recipient.

## Deliverables

- **The target resolved to one place.** A specific person or channel, not a guess.
- **The draft is in the recipient's language.** When it cannot be inferred from
  their node or the context, the person was asked and the answer was stored on
  that `person` node.
- **The register matches the reader.** Mechanism, meaning identifiers, stack
  traces, file paths and config keys, goes to developers and to anyone who asked
  for detail. Everyone else gets consequence: what breaks, for whom, starting
  when, and what decision it forces. The technical version is relocated to the
  ticket, the PR or the brain, never deleted.
- **It sounds like the person, not like a model.** The observed traits of their
  samples are mirrored: cadence, warmth, formality, sign-off.
- **It was approved before it went out.** The resolved recipient and the exact
  text were shown and agreed, unless the person had already authorized this send.
- **The interaction is in memory**, so the next conversation with that person
  starts with context.

## Judged against

- **Name the reader from the draft alone.** If a reader cannot tell who it was
  written for, it was written for the sender.
- **The message language matches the recipient's `props.lang`.**
- **A non-developer recipient's message contains no identifier they have no reason
  to know.** A correct finding at the wrong register lands as noise.
- **The exact sent text appears in the exchange above the send.**

## Guardrails — must not

- **Must not send without confirmation.** It goes out under the person's name, to
  a colleague or a customer.
- **Must not send to a channel it was not asked to.** When the target is unclear,
  ask.
- **Must not default to English.** Read `props.lang`, and when unknown, ask and
  store it.
- **Must not explain mechanism to a reader who cannot act on it.** That is the
  failure this skill most often makes: the finding is right, the register is
  wrong, and it lands as noise.

## Reply (focus contract)

Show the resolved recipient and the exact text, then ask for the yes. After
sending, one line confirming where it landed.
