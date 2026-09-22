import { describe, expect, it } from "vitest";
import {
  countSkills,
  effectiveRealms,
  emptySkillFilter,
  keepProcess,
  keepRule,
  keepSkill,
  ruleHaystack,
  skillHaystack,
} from "./filter";
import type { Source } from "./filter";
import { skillsFixture } from "@/test/fixtures";
import type { SkillNode } from "@/api/types";

const recall = skillsFixture.trees[0]!.skills[0]!;
const f = (over: Partial<ReturnType<typeof emptySkillFilter>> = {}) => ({ ...emptySkillFilter(), ...over });

describe("skillHaystack", () => {
  it("covers every field a person might reach for", () => {
    const hay = skillHaystack(recall);
    expect(hay).toContain("recall");
    expect(hay).toContain("read the graph back");
    expect(hay).toContain("what do i know about");
    expect(hay).toContain("task");
  });

  it("leaves the always-on rules out, or one query returns the whole corpus", () => {
    const hay = skillHaystack(recall);
    expect(hay).toContain("the memory graph api"); // declared
    expect(hay).not.toContain("always-on capture"); // non-negotiable, on every skill
  });
});

describe("keepSkill", () => {
  it("keeps a skill that matches", () => {
    expect(keepSkill(recall, "plugin", f({ q: "recall" }))?.dim).toBe(false);
  });

  it("drops one that does not", () => {
    expect(keepSkill(recall, "plugin", f({ q: "invoice" }))).toBeNull();
  });

  it("keeps a parent whose descendant matched, and dims it", () => {
    const kept = keepSkill(recall, "plugin", f({ q: "visualize" }));
    expect(kept).not.toBeNull();
    expect(kept!.dim).toBe(true);
    expect(kept!.children.map((c) => c.id)).toEqual(["skill_visualize"]);
  });

  it("filters by source, treating user and learned as the same tree", () => {
    expect(keepSkill(recall, "plugin", f({ src: new Set(["plugin"] as Source[]) }))).not.toBeNull();
    expect(keepSkill(recall, "plugin", f({ src: new Set(["user"] as Source[]) }))).toBeNull();
    expect(keepSkill(recall, "learned", f({ src: new Set(["learned"] as Source[]) }))).not.toBeNull();
    expect(keepSkill(recall, "learned", f({ src: new Set(["user"] as Source[]) }))).not.toBeNull();
  });

  it("filters by domain", () => {
    expect(keepSkill(recall, "plugin", f({ dom: "hub_process" }))).not.toBeNull();
    expect(keepSkill(recall, "plugin", f({ dom: "hub_writing" }))).toBeNull();
  });

  it("is case-insensitive", () => {
    expect(keepSkill(recall, "plugin", f({ q: "RECALL" }))).not.toBeNull();
  });
});

describe("keepRule", () => {
  const [emoji, graphApi] = skillsFixture.rules;

  it("matches the cue as well as the title, because match-rules fires on the cue", () => {
    expect(keepRule(emoji!, f({ q: "writing anything" }))).toBe(true);
    expect(ruleHaystack(emoji!)).toContain("writing anything for a person");
  });

  it("filters by source and domain", () => {
    expect(keepRule(emoji!, f({ src: new Set(["user"] as Source[]) }))).toBe(true);
    expect(keepRule(emoji!, f({ src: new Set(["plugin"] as Source[]) }))).toBe(false);
    expect(keepRule(emoji!, f({ src: new Set(["plugin", "user"] as Source[]) }))).toBe(true);
    expect(keepRule(graphApi!, f({ dom: "hub_process" }))).toBe(true);
    expect(keepRule(graphApi!, f({ dom: "hub_writing" }))).toBe(false);
  });

  it("keeps everything when nothing is asked", () => {
    expect(keepRule(emoji!, f())).toBe(true);
  });
});

describe("countSkills", () => {
  it("counts every level, not just the roots", () => {
    const kept = keepSkill(recall, "plugin", f())!;
    expect(countSkills([kept])).toBe(2);
  });

  it("is zero for nothing", () => {
    expect(countSkills([])).toBe(0);
  });

  it("counts a deep branch", () => {
    const deep: SkillNode = {
      ...recall,
      children: [{ ...recall, id: "b", children: [{ ...recall, id: "c", children: [] }] }],
    };
    expect(countSkills([keepSkill(deep, "plugin", f())!])).toBe(3);
  });
});

describe("keepProcess", () => {
  const [mine, theirs] = skillsFixture.processes!;
  const cur = effectiveRealms(emptySkillFilter(), "optibus");

  it("follows the current realm until the realm facet is touched", () => {
    expect(keepProcess(mine!, f(), cur)).toBe(true);
    expect(keepProcess(theirs!, f(), cur)).toBe(false);
  });

  it("reads another realm once it is picked", () => {
    const picked = f({ realms: new Set(["personal"]) });
    expect(keepProcess(theirs!, picked, effectiveRealms(picked, "optibus"))).toBe(true);
    expect(keepProcess(mine!, picked, effectiveRealms(picked, "optibus"))).toBe(false);
  });

  it("matches words in the steps and the trigger", () => {
    expect(keepProcess(mine!, f({ q: "catch up" }), cur)).toBe(true);
    expect(keepProcess(mine!, f({ q: "my epics" }), cur)).toBe(true);
    expect(keepProcess(mine!, f({ q: "invoice" }), cur)).toBe(false);
  });

  it("keeps a process when user is one of several sources ticked", () => {
    expect(keepProcess(mine!, f({ src: new Set(["plugin", "user"] as Source[]) }), cur)).toBe(true);
    expect(keepProcess(mine!, f({ src: new Set() }), cur)).toBe(false);
  });

  it("is never built-in or learned, so those sources hide it", () => {
    expect(keepProcess(mine!, f({ src: new Set(["plugin"] as Source[]) }), cur)).toBe(false);
    expect(keepProcess(mine!, f({ src: new Set(["user"] as Source[]) }), cur)).toBe(true);
  });
});
