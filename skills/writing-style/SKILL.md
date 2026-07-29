---
name: writing-style
description: Learn and apply the person's writing style. Use to capture a sample of how they write ("this is my voice", "save this email as a style example"), and to draft anything — Slack messages, emails, replies — so it sounds like them, not like an AI. Any skill that writes text on the person's behalf should apply this first.
---

# writing-style

Keep the person's voice in memory and write in it, so delegating the *writing*
doesn't cost them their authenticity.

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## Capturing a sample

When the person shares something they wrote (or point to a message as "this is
how I write"):
1. `add-node --type writing-sample --title "<context, e.g. 'Slack reply to
   teammate'>" --body "<the verbatim text>"
   --props '{"register":"casual|neutral|formal","medium":"slack|email|doc"}'`.
2. Link it `authored-by` the person node.
3. Note the observable traits in the body or a linked `association`: sentence
   length, greeting/sign-off habits, emoji use, directness, punctuation quirks,
   words they favor or avoid.

## Applying the style

When drafting on their behalf:
1. `search --type writing-sample` (and `neighbors` on the person) to pull
   samples, preferring the same medium/register as the target.
2. Mirror the observed traits — cadence, warmth, formality, emoji, sign-off.
   Match, don't caricature.
3. Keep the focus contract for *your* framing to the person (short, one action),
   but the drafted content should sound fully like them.
4. Show the draft for a quick yes before anything is sent outward.

## Notes
- The more samples across mediums, the better. Encourage saving good ones.
- If there are no samples yet, ask for one, or draft neutrally and flag that
  you're guessing at their voice.
