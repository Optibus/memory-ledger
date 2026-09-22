/**
 * The sentence under each contract field.
 *
 * The HEADINGS come from `/api/meta` (`skill_sections`, `rule_sections`) so the
 * shape has one definition, in the engine. These are the guidance beside them —
 * what a good answer looks like, which is a property of the form, not of §5.1.
 */
export const RULE_HINTS: Record<string, string> = {
  the_rule: "What must or must not happen.",
  why: "The failure it prevents. A rule whose reason is missing loses every argument with a deadline.",
  how_to_apply: "What to do concretely, at the moment the rule bites.",
  exceptions:
    "Where it does NOT apply. Left blank this is written as `none`, because an unstated exception gets invented under pressure.",
  judged_against:
    "How a reader can tell from the artifact ALONE that it was followed. This is the section that does the work.",
  related: "[[wikilinks]] to the rules this one leans on.",
};

export const SKILL_HINTS: Record<string, string> = {
  when_to_use: "The situations that should make this fire — in the words you would actually type.",
  given: "What has to already exist for it to run: inputs, access, state.",
  deliverables: "What EXISTS when it finishes. Artifacts, not activities.",
  judged_against: "How someone checks it was done right, from the output alone.",
  guardrails: "What it may never do, however sensible it seems at the time.",
  reply: "What it says back when it is done.",
};

export const DECL_HINTS: Record<string, string> = {
  requires: "The skills that must have run first. This is the only edge that nests the tree.",
  waits_for: "Skills it waits on without depending on their output.",
  enriched_by: "Skills that make it better when present, and that it works without.",
  reads: "Node types it reads. Two skills touching the same type are coupled whether or not either names the other.",
  writes: "Node types it writes.",
};

/** `## When to use` reads as "When to use" on a form label. */
export function labelOf(heading: string): string {
  return heading.replace(/^#+\s*/, "").trim();
}
