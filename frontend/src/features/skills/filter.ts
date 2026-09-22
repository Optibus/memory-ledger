import type { RuleNode, SkillNode } from "@/api/types";

export interface SkillFilter {
  q: string;
  show: "all" | "skills" | "rules";
  src: "all" | "plugin" | "user" | "learned";
  dom: string;
}

export function emptySkillFilter(): SkillFilter {
  return { q: "", show: "all", src: "all", dom: "all" };
}

/**
 * One text box over every field that can identify an item.
 *
 * The words a person reaches for ("slack", "task", "before I send") live in
 * different fields depending on whether the thing is a skill or a rule, and
 * asking them to know which would be asking them to know the schema.
 */
export function skillHaystack(sk: SkillNode): string {
  return [
    sk.id,
    sk.title,
    sk.description,
    sk.triggers,
    sk.reads.join(" "),
    sk.writes.join(" "),
    sk.reads_other,
    sk.writes_other,
    sk.domains.join(" "),
    // Only the rules this skill pulled SPECIFICALLY. The always-on ones are on
    // every skill, so matching them would return the whole corpus for a query
    // like "dashes" — true, and useless.
    sk.rules
      .filter((r) => r.why !== "always")
      .map((r) => `${r.id} ${r.title}`)
      .join(" "),
  ]
    .join(" ")
    .toLowerCase();
}

export function ruleHaystack(r: RuleNode): string {
  return [r.id, r.title, r.scenario, r.section, r.source, (r.domains || []).join(" ")]
    .join(" ")
    .toLowerCase();
}

export interface KeptSkill extends SkillNode {
  children: KeptSkill[];
  /** Kept only because a descendant matched — shown, but dimmed. */
  dim: boolean;
}

/**
 * A parent survives if any descendant matches, so filtering never orphans a
 * child by hiding the branch it hangs from.
 */
export function keepSkill(sk: SkillNode, treeKind: string, f: SkillFilter): KeptSkill | null {
  const kids = sk.children
    .map((c) => keepSkill(c, treeKind, f))
    .filter((x): x is KeptSkill => x !== null);

  const srcOk =
    f.src === "all" ||
    (f.src === "plugin" ? treeKind === "plugin" : treeKind === "learned");
  const domOk = f.dom === "all" || sk.domains.includes(f.dom);
  const qOk = !f.q || skillHaystack(sk).includes(f.q.toLowerCase());
  const self = srcOk && domOk && qOk;

  if (kids.length || self) return { ...sk, children: kids, dim: !self };
  return null;
}

export function keepRule(r: RuleNode, f: SkillFilter): boolean {
  if (f.src !== "all" && r.source !== f.src) return false;
  if (f.dom !== "all" && !(r.domains || []).includes(f.dom)) return false;
  return !f.q || ruleHaystack(r).includes(f.q.toLowerCase());
}

/** How many skills a filtered tree actually holds, counting every level. */
export function countSkills(list: KeptSkill[]): number {
  return list.reduce((n, s) => n + 1 + countSkills(s.children), 0);
}
