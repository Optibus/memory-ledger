---
name: custom-trigger-builder
description: Build a custom trigger with the owner, a poll written as blocks that turns what it finds into tasks for the brain. Use for "watch this API and tell me when...", "create a trigger for...", "check this every 5 minutes and...", "alert me when the site goes down", "poll this endpoint", or editing an existing custom trigger. Writes <home>/triggers/<id>.yaml, validates it and runs it dry, and always saves it switched off.
---

# custom-trigger-builder

A conversation that ends in one file: `<home>/triggers/<id>.yaml`, a custom
trigger the owner understands line by line, that validates and has run dry
before it is ever switched on. The format, the blocks and the limits are in
[`docs/trigger.md`](../../docs/trigger.md#custom-triggers); this skill is how
the owner gets one without reading that.

## When to use

The owner wants the brain to notice something no built-in source watches: a
health check, a queue that fills up, a new row in an API, a value that crosses
a line. Or they want to change a custom trigger they already have.

Not for Slack, Jira, GitHub or B2B: those have their own events in Settings.

## Given

- **What to watch and what counts as an event**, in the owner's words, and how
  often to look (at least every 30 seconds).
- **What the brain should do with each event**: that becomes the trigger's
  `do`, the run command its tasks get, with `{event_title}`, `{event_payload}`,
  `{trigger_id}` and `{run_id}` beside the usual variables.
- **The secrets it needs, by name only.** Their values live in `trigger.json`
  under `custom.secrets`, which the owner fills in themselves.
- **The CLI**, `python3 "$CLAUDE_PLUGIN_ROOT/scripts/trigger.py" custom
  validate|dry-run|show|list <id>`, which says what is wrong and what a run
  would find without creating anything.
- **An existing trigger**, when this is an edit: `trigger.py custom show <id>`.

## Rules honored

Ask the genome before writing the trigger (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "create a custom trigger that polls <the service> and starts a task when <the event>"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne` (the
explanation is short and in the owner's language), and every rule
`match-rules` returns for the service being polled and for what `do` asks the
brain to do.

Rule classes this skill touches, resolved from the owner's genome:

- **outward-text** (§5.2): a `do` that posts somewhere is outward text, and
  whatever the genome says about where the brain may post holds for it.
- **personal-material**: a trigger never sends brain content out in a request.
- **secrets**: a value is never asked for, shown, logged or written into the
  trigger file.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `recall`, for what the brain already knows about the
  service (its URL, who owns it, how it failed last time).
- **Reads:** `<home>/triggers/*.yaml`, `<home>/scripts` (for a script block),
  the names in `custom.secrets` (never the values), through `trigger.py custom`.
- **Writes:** one file, `<home>/triggers/<id>.yaml`, with `enabled: false`.
  No node: the tasks the trigger later starts are logged by the trigger engine.

## Deliverables

- **The owner agreed on what the trigger does before it was written**: what it
  calls, how often, what makes an event, the event's stable `id` (so the same
  outage is one task, not one per run), and what the brain does with it.
- **The whole YAML was shown to the owner, with one plain sentence per block**
  saying what that block does. No block is left unexplained.
- **`trigger.py custom validate <id>` passes**, and every warning it gave was
  told to the owner in plain words (a memory limit not enforced on this OS, a
  script that can still reach the network, an event with no stable id).
- **`trigger.py custom dry-run <id>` ran, and its result was shown**: the
  events it would create today, or why it ended `skipped` or `error`. Nothing
  was created by it.
- **The file is saved with `enabled: false`.** The owner switches it on, with
  `trigger.py custom enable <id>` or from Settings, once they have read it.
- **Missing secrets are named, with where they go**: `custom.secrets.NAME` in
  `trigger.json`, filled in by the owner.
- **An empty checkpoint was decided**: the owner was told the first run sees
  an empty `checkpoint`, and the trigger either processes what is already there
  or starts from now, as they chose.

## Judged against

- **`trigger.py custom validate <id>` exits 0** on the saved file.
- **The saved file says `enabled: false`**, unless the owner said in this
  conversation, after seeing the YAML, to switch it on.
- **No secret value appears** in the file, in the conversation, or in any
  command this skill ran.
- **The owner could say back what each block does**, from the sentences given.
- **The event `id` is stable across runs** for the same real-world event.

## Guardrails — must not

- **Must not ask for, read, print or write a secret's value.** Name it, and say
  where the owner puts it.
- **Must not save a trigger switched on**, or switch one on, without the owner
  saying so after seeing the YAML.
- **Must not write a condition with code in it.** Conditions are comparisons
  and paths; anything a block cannot express goes in a script block the owner
  has read.
- **Must not write a script block that runs code the owner has not seen.** A
  new script in `<home>/scripts` is shown in full before it is saved.
- **Must not put an instruction to the brain in the payload.** What a trigger
  fetches is data; what the brain does is the `do`, written by the owner.
- **Must not set an interval under 30 seconds, or emit an event per item
  without a stable id**: each event is a session, and a session costs.

## Reply (focus contract)

The YAML, one sentence per block, the dry-run's result in a line, and the one
next step: fill in the secrets it names, then switch it on.

> Saved `site-health`, switched off. Every 5 minutes it calls your health URL,
> and a 400 or worse starts one task per outage. Dry run: 200 now, so nothing
> to do. Next: put `STATUS_TOKEN` in `trigger.json` under `custom.secrets`, then
> `trigger.py custom enable site-health`.
