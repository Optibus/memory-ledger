---
name: learn-skill
description: The neuroplasticity skill — teach the brain a new, reusable ability at runtime and remember WHEN to use it, so it can apply the learning to future situations in any project. Use when the person says "remember how to do this", "learn this workflow", "next time do it this way", "make this a skill", or when you notice a repeatable procedure worth keeping. Also the skill that RECALLS and REINFORCES learned skills.
---

# learn-skill

This is how the brain grows. A learned skill is a `skill` **node in the graph**,
so its triggers and its full procedure live in the DB and fire in any project, on
any machine, from the phone.

## When to use

Three moments, and the middle one is the one that gets skipped:

- **Learn**, when the person teaches a procedure or you just did one that worked.
- **Recall**, at the start of handling any request, before improvising.
- **Reinforce**, right after a learned skill actually helped.

## Given

- **The procedure**, either as the person taught it or as you just performed it.
- **The situations it fits, in the person's own words.** Fuzzy phrasing is correct
  here, because matching is associative and fires on what they would actually type.
- **The existing skill nodes**, so a near-duplicate updates rather than forks.

## Rules honored

Ask the genome for the rules that apply before learning or recalling (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "learn a repeatable procedure as a skill and recall it later"
```

Accountable for: `rule_s2b_learned-skills-neuroplasticity`.

Rule classes this skill touches, resolved from the person's own genome:

- **durable-storage** (§5.2), via `match-rules "store a durable fact"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Reads:** `skill`, through `match-skills` for recall and `get-node` for the
  procedure body.
- **Writes:** `skill` nodes via `add-skill`, stamped `kind: learned` with their
  §5a declarations in props, usage via `use-skill`, and the edges `learned-from`,
  `applies-to`, `relates-to`.

## Deliverables

- **The skill is a node, not a file.** `add-skill` stored it in the DB, which is
  the source of truth and syncs everywhere. A local `SKILL.md` export happens only
  with `--with-file`, and is a throwaway.
- **Its triggers are in the person's words.** The `--when` field carries the
  situations they would actually describe, including phrasings the procedure body
  never uses, because that field is what `match-skills` fires on.
- **The procedure is followable cold.** Concrete and ordered enough that someone
  who was not there can run it.
- **It is wired into the graph.** Linked `learned-from` whatever taught it,
  `applies-to` the project or task type it serves, `relates-to` sibling skills, so
  the learning transfers instead of sitting in one situation.
- **It declares its contract, so it joins the user tree.** `--requires`,
  `--waits-for`, `--enriched-by`, `--reads` and `--writes` say which skills it
  depends on and which node types it touches, in the same vocabulary a built-in
  skill uses (§5a). `sync-skill-tree` then hangs it under `hub_skills_user` with
  real edges instead of as a leaf, and a user skill may `require` a built-in one.
  A skill that declares nothing still works, it is just invisible to anything
  reasoning about what touches what.
- **Recall happened before improvising.** At the start of a request,
  `match-skills` ran, and a match was read from its node body and followed.
- **A skill that fired was reinforced.** `use-skill --id` ran, so proven skills
  surface first among equally relevant matches.

## Judged against

- **`match-skills "<the situation in the person's words>"` returns it.** A skill
  that does not fire on its own trigger phrasing does not exist in practice.
- **`sync-skill-tree` places it under `hub_skills_user`** with the edges it
  declared, and re-running the sync changes nothing.
- **Re-running `add-skill` with the same `--name` updated one node**, rather than
  creating a second.
- **Its edges point somewhere.** An unlinked skill only fires on exact recall,
  which is the failure mode this whole layer exists to avoid.

## Guardrails — must not

- **Must not write the procedure only to a markdown file.** That isolates it to
  one machine, which is the design this replaced.
- **Must not write a megaskill.** Small, single-purpose skills compose. A large
  one fires on everything and helps with nothing.
- **Must not leave a misfiring skill in place.** Say so, then fix its triggers or
  lower its relevance, because a skill that fires wrongly costs more than none.

## Reply (focus contract)

One line: what was learned, and when it will fire.

> Learned **end-of-day-review**. I will reach for it whenever you are wrapping up
> the day, and it gets sharper each time we use it.

To bring existing markdown skills in once: `graph.py ingest-skills` scans the
plugin `skills/` and any `learned_skills/` and upserts each as a node.
