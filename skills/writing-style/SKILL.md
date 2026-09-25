---
name: writing-style
description: Learn and apply the person's writing style, and match the register to whoever is reading. Use to capture a sample of how they write ("this is my voice", "save this email as a style example"), and to draft anything — Slack messages, emails, replies, ticket comments — so it sounds like them, not like an AI, and lands with its actual audience. Any skill that writes text on the person's behalf applies this first.
---

# writing-style

Keep the person's voice in memory and write in it, so delegating the writing does
not cost them their authenticity. Two things have to be true at once: it sounds
like them, and it is pitched at whoever is reading it.

## When to use

Any time text is drafted on the person's behalf, and any time they point at
something and say "this is how I write".

## Given

- **Their `writing-sample` nodes**, preferring the same medium and register as the
  target.
- **The reader.** Who receives this, and what they can act on. Naming the reader
  comes before drafting, because a draft with no named reader was written for the
  writer.
- **The audience-specific skills this brain has learned.** Run
  `match-skills "<the reader's role>"`. A learned communication skill for that
  audience is more precise than anything this file can say, because it was built
  from this person's own corrections.

## Rules honored

Ask the genome for the rules that apply before drafting (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "draft a message to <recipient>"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne`.

Rule classes this skill touches, resolved from the person's own genome:

- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

The person's own rules about punctuation, emoji, register and length live in
their brain, not in this file. This skill's job is to fetch them and obey them,
not to restate them.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `learn-skill`, when a correction to a draft reveals a
  repeatable audience rule worth keeping as its own skill.
- **Reads:** `writing-sample`, `person`, `skill` and `rule`, through
  `match-skills` and `match-rules`.
- **Writes:** `writing-sample` nodes with
  `props {register, medium}`, linked `authored-by` the person.

## Deliverables

- **A captured sample is usable later.** It is stored verbatim, with the context
  it came from as its title, its register and medium in props, and linked
  `authored-by` the person. The observable traits are noted alongside it:
  sentence length, greeting and sign-off habits, emoji use, directness,
  punctuation quirks, words favoured and words avoided.
- **The reader is named before the draft exists.**
- **The draft mirrors the samples**, matching rather than caricaturing. Cadence,
  warmth, formality, sign-off.
- **The register matches the reader, not the work.** Mechanism for developers and
  for anyone who asked for detail. Consequence for everyone else: what breaks, for
  whom, starting when, what decision it forces. Where a technical term is genuinely
  load bearing, it is named once and explained in the same sentence.
- **The technical version is relocated, not lost.** It belongs in the ticket, the
  PR or the brain, where the implementer will look for it.
- **The person's framing stays short.** The focus contract governs what you say to
  them. It does not govern the draft, which should sound fully like them.
- **Nothing went out unreviewed.**

## Judged against

- **A reader who sees only the draft can name its intended audience.**
- **For a mixed audience, the least technical person who has to act can still
  act.** The detail is linked, not inlined.
- **The voice matches a sample.** If the draft reads like the model, the mirroring
  clause failed, whatever the content.
- **A captured sample can be found by medium and register.**

## Guardrails — must not

- **Must not caricature the person's voice.** Exaggerated quirks read worse than
  neutral text.
- **Must not send before showing the draft.**
- **Must not guess at a voice silently.** With no samples, ask for one, or draft
  neutrally and say plainly that you are guessing.
- **Must not restate the person's writing rules in this file.** They live in the
  brain, and a copy here goes stale the moment they change one.
- **Must not write mechanism to a reader who cannot act on it.** The finding may be
  correct and still land as noise.

## Reply (focus contract)

The draft, then one line naming who it is pitched at and asking for the yes.

> Drafted for **Priya, product owner**, so it leads with what the customer sees
> and leaves the parser detail in the ticket. Send it?
