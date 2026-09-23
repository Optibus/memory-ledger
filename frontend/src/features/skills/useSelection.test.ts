import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { kindOf, useSelection } from "./useSelection";

describe("kindOf", () => {
  it("reads the corpus off the id", () => {
    expect(kindOf("skill_recall")).toBe("skills");
    expect(kindOf("rule_user_no-emoji")).toBe("rules");
    expect(kindOf("process_refine-an-epic_0693")).toBe("processes");
    // a process retyped from a decision keeps its id
    expect(kindOf("decision_use-jira-flags", new Set(["decision_use-jira-flags"]))).toBe("processes");
    expect(kindOf("decision_use-jira-flags")).toBe("rules");
    expect(kindOf("script:dev_env.py")).toBe("scripts");
  });
});

describe("useSelection", () => {
  it("toggles an id on and off", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggle("skill_a"));
    expect(result.current.picked.has("skill_a")).toBe(true);
    act(() => result.current.toggle("skill_a"));
    expect(result.current.picked.has("skill_a")).toBe(false);
  });

  it("honours an explicit want, so a checkbox cannot get out of step", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggle("skill_a", true));
    act(() => result.current.toggle("skill_a", true));
    expect(result.current.picked.size).toBe(1);
  });

  it("splits the selection the way the bundle endpoints want it", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.selectAll(["skill_a", "rule_b", "skill_c"]));
    act(() => result.current.selectAll(["skill_a", "rule_b", "skill_c", "process_d", "script:x.sh"]));
    expect(result.current.split()).toEqual({
      skills: ["skill_a", "skill_c"],
      rules: ["rule_b"],
      processes: ["process_d"],
      scripts: ["x.sh"],
    });
  });

  it("drops the selection when selection mode is left, so no hidden tick survives", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.setPicking(true));
    act(() => result.current.selectAll(["skill_a"]));
    act(() => result.current.setPicking(false));
    expect(result.current.picked.size).toBe(0);
  });
});
