import { describe, expect, it } from "vitest";
import {
  computeCascade,
  deserializeFilter,
  directHidden,
  emptyFilter,
  filteredOut,
  isHidden,
  isShaded,
  matchesQuery,
  serializeFilter,
} from "./graphFilter";
import type { GraphEdge, GraphNode } from "@/api/types";

const node = (id: string, over: Partial<GraphNode> = {}): GraphNode => ({
  id,
  title: id,
  type: "task",
  realm: "optibus",
  ...over,
});

const chain = (): { nodes: GraphNode[]; edges: GraphEdge[] } => ({
  // A -> B -> C -> D,  E -> C
  nodes: ["A", "B", "C", "D", "E"].map((id) => node(id)),
  edges: [
    { src: "A", dst: "B", rel: "relates-to" },
    { src: "B", dst: "C", rel: "relates-to" },
    { src: "C", dst: "D", rel: "relates-to" },
    { src: "E", dst: "C", rel: "relates-to" },
  ],
});

describe("directHidden", () => {
  it("drops a node whose type is switched off", () => {
    const f = { ...emptyFilter(), typesOff: new Set(["task"]) };
    expect(directHidden(node("a"), f)).toBe(true);
    expect(directHidden(node("b", { type: "person" }), f)).toBe(false);
  });

  it("treats a missing realm as the empty realm rather than ignoring the filter", () => {
    const f = { ...emptyFilter(), realmsOff: new Set([""]) };
    expect(directHidden(node("a", { realm: "" }), f)).toBe(true);
  });

  it("only hides done nodes when hide-done is on", () => {
    const n = node("a", { status: "done" });
    expect(directHidden(n, emptyFilter())).toBe(false);
    expect(directHidden(n, { ...emptyFilter(), hideDone: true })).toBe(true);
  });

  it("leaves an open node alone with hide-done on", () => {
    expect(directHidden(node("a", { status: "doing" }), { ...emptyFilter(), hideDone: true })).toBe(false);
  });
});

describe("computeCascade", () => {
  it("is empty when the toggle is off, however much is filtered", () => {
    const { nodes, edges } = chain();
    const f = { ...emptyFilter(), typesOff: new Set(["task"]) };
    expect(computeCascade(nodes, edges, f).size).toBe(0);
  });

  it("drops what lost its only support, and keeps what still has one", () => {
    const { nodes, edges } = chain();
    // Hiding A takes B with it. C survives on E, so D does too.
    const f = { ...emptyFilter(), cascade: true, typesOff: new Set<string>() };
    const hideA = { ...f, realmsOff: new Set(["hidden"]) };
    const withA = nodes.map((n) => (n.id === "A" ? { ...n, realm: "hidden" } : n));
    const off = computeCascade(withA, edges, hideA);
    expect([...off]).toEqual(["B"]);
  });

  it("unwinds the whole chain when every support is gone", () => {
    const { nodes, edges } = chain();
    const marked = nodes.map((n) => (["A", "E"].includes(n.id) ? { ...n, realm: "hidden" } : n));
    const f = { ...emptyFilter(), cascade: true, realmsOff: new Set(["hidden"]) };
    const off = computeCascade(marked, edges, f);
    expect([...off].sort()).toEqual(["B", "C", "D"]);
  });

  it("never drops a root — nothing points at it, so nothing can be taken away", () => {
    const { nodes, edges } = chain();
    const f = { ...emptyFilter(), cascade: true };
    expect(computeCascade(nodes, edges, f).size).toBe(0);
  });

  it("does not count a self-loop as support", () => {
    const nodes = [node("A", { realm: "hidden" }), node("B")];
    const edges: GraphEdge[] = [
      { src: "A", dst: "B", rel: "relates-to" },
      { src: "B", dst: "B", rel: "relates-to" },
    ];
    const f = { ...emptyFilter(), cascade: true, realmsOff: new Set(["hidden"]) };
    expect([...computeCascade(nodes, edges, f)]).toEqual(["B"]);
  });

  it("lets a cycle hold itself up", () => {
    const nodes = [node("A", { realm: "hidden" }), node("B"), node("C")];
    const edges: GraphEdge[] = [
      { src: "A", dst: "B", rel: "relates-to" },
      { src: "B", dst: "C", rel: "relates-to" },
      { src: "C", dst: "B", rel: "relates-to" },
    ];
    const f = { ...emptyFilter(), cascade: true, realmsOff: new Set(["hidden"]) };
    expect(computeCascade(nodes, edges, f).size).toBe(0);
  });
});

describe("hidden vs shaded", () => {
  it("reaches exactly the same set either way", () => {
    const { nodes, edges } = chain();
    const marked = nodes.map((n) => (n.id === "A" ? { ...n, realm: "hidden" } : n));
    const base = { ...emptyFilter(), cascade: true, realmsOff: new Set(["hidden"]) };
    const off = computeCascade(marked, edges, base);

    const hiddenSet = marked.filter((n) => isHidden(n, base, off)).map((n) => n.id);
    const shadeMode = { ...base, shade: true };
    const shadedSet = marked.filter((n) => isShaded(n, shadeMode, off)).map((n) => n.id);

    expect(shadedSet).toEqual(hiddenSet);
    // And the one predicate they both come from agrees with both.
    expect(marked.filter((n) => filteredOut(n, base, off)).map((n) => n.id)).toEqual(hiddenSet);
  });

  it("hides nothing in shade mode, and shades nothing in hide mode", () => {
    const n = node("a", { type: "task" });
    const off = new Set<string>();
    const hide = { ...emptyFilter(), typesOff: new Set(["task"]) };
    expect(isHidden(n, hide, off)).toBe(true);
    expect(isShaded(n, hide, off)).toBe(false);

    const shade = { ...hide, shade: true };
    expect(isHidden(n, shade, off)).toBe(false);
    expect(isShaded(n, shade, off)).toBe(true);
  });
});

describe("matchesQuery", () => {
  const n = node("task_ship-ui_a1", { title: "Ship the new UI", type: "task" });

  it("matches on title, type and id, case-insensitively", () => {
    expect(matchesQuery(n, "ship")).toBe(true);
    expect(matchesQuery(n, "TASK")).toBe(true);
    expect(matchesQuery(n, "a1")).toBe(true);
  });

  it("matches everything when the query is empty", () => {
    expect(matchesQuery(n, "")).toBe(true);
  });

  it("does not match what is not there", () => {
    expect(matchesQuery(n, "invoice")).toBe(false);
  });
});

describe("filter persistence", () => {
  it("survives a round trip through JSON, Sets included", () => {
    const f = emptyFilter();
    f.typesOff.add("capture");
    f.realmsOff.add("personal");
    f.hideDone = true;
    f.shade = true;

    const back = deserializeFilter(JSON.parse(JSON.stringify(serializeFilter(f))));

    expect([...back.typesOff]).toEqual(["capture"]);
    expect([...back.realmsOff]).toEqual(["personal"]);
    expect(back.hideDone).toBe(true);
    expect(back.shade).toBe(true);
    expect(back.cascade).toBe(false);
  });

  it("degrades to the empty filter rather than hiding nodes for no reason", () => {
    for (const junk of [null, undefined, "nope", 42, [], { typesOff: "capture" }]) {
      const f = deserializeFilter(junk);
      expect(f.typesOff.size).toBe(0);
      expect(f.hideDone).toBe(false);
    }
  });

  it("drops non-strings inside a facet list", () => {
    const f = deserializeFilter({ typesOff: ["task", 7, null, "note"] });
    expect([...f.typesOff]).toEqual(["task", "note"]);
  });
});
