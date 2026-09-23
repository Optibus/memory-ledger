import type { ProcessNode, RuleNode, ScriptEntry, SkillNode } from "@/api/types";

/** The four kinds of thing the page lists, in page order. Multi-select: any mix is a view. */
export type ItemType = "skill" | "rule" | "process" | "script";
export const ITEM_TYPES: ItemType[] = ["skill", "rule", "process", "script"];

/** Who authored a thing. Multi-select, like the types. */
export type Source = "plugin" | "user" | "learned";
export const SOURCES: Source[] = ["plugin", "user", "learned"];

export interface SkillFilter {
  q: string;
  types: Set<ItemType>;
  src: Set<Source>;
  dom: string;
  /**
   * Which realms' processes are shown. `null` means "the current realm" — the
   * page does not know which that is until the payload arrives, and a person who
   * never touched the facet should keep following it if the setting changes.
   */
  realms: Set<string> | null;
}

export function emptySkillFilter(): SkillFilter {
  return { q: "", types: new Set(ITEM_TYPES), src: new Set(SOURCES), dom: "all", realms: null };
}

/** Every source ticked: nothing is filtered, including a source outside the three. */
export function allSources(f: SkillFilter): boolean {
  return SOURCES.every((s) => f.src.has(s));
}

/** The realms a filter actually shows, with `null` resolved to the current one. */
export function effectiveRealms(f: SkillFilter, current: string): Set<string> {
  return f.realms ?? new Set(current ? [current] : []);
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

  // The learned tree is where both user-authored and learned skills live, so
  // either source keeps it; only the plugin tree is built-in.
  const srcOk =
    allSources(f) ||
    (treeKind === "plugin" ? f.src.has("plugin") : f.src.has("user") || f.src.has("learned"));
  const domOk = f.dom === "all" || sk.domains.includes(f.dom);
  const qOk = !f.q || skillHaystack(sk).includes(f.q.toLowerCase());
  const self = srcOk && domOk && qOk;

  if (kids.length || self) return { ...sk, children: kids, dim: !self };
  return null;
}

export function keepRule(r: RuleNode, f: SkillFilter): boolean {
  if (!allSources(f) && !f.src.has(r.source as Source)) return false;
  if (f.dom !== "all" && !(r.domains || []).includes(f.dom)) return false;
  return !f.q || ruleHaystack(r).includes(f.q.toLowerCase());
}

export function processHaystack(p: ProcessNode): string {
  return [p.id, p.title, p.body, p.trigger, p.realm, p.category, p.domains.join(" ")]
    .join(" ")
    .toLowerCase();
}

/**
 * A process has no built-in/learned tree, so only the `user` source keeps it:
 * every process was written in some brain by a person, never shipped as a file.
 */
export function keepProcess(p: ProcessNode, f: SkillFilter, realms: Set<string>): boolean {
  if (!realms.has(p.realm)) return false;
  if (!allSources(f) && !f.src.has("user")) return false;
  if (f.dom !== "all" && !p.domains.includes(f.dom)) return false;
  return !f.q || processHaystack(p).includes(f.q.toLowerCase());
}

export function scriptHaystack(sc: ScriptEntry): string {
  return [sc.name, sc.summary, sc.language].join(" ").toLowerCase();
}

/**
 * A script is a file a person wrote, so only the `user` source keeps it, and it
 * has no domain: narrowing to one hides every script, as it would a rule
 * tagged with no domain.
 */
export function keepScript(sc: ScriptEntry, f: SkillFilter): boolean {
  if (!allSources(f) && !f.src.has("user")) return false;
  if (f.dom !== "all") return false;
  return !f.q || scriptHaystack(sc).includes(f.q.toLowerCase());
}

/** How many skills a filtered tree actually holds, counting every level. */
export function countSkills(list: KeptSkill[]): number {
  return list.reduce((n, s) => n + 1 + countSkills(s.children), 0);
}
