import { describe, expect, it } from "vitest";
import { applyLayout, egoLayout, pickEgo } from "./egoLayout";
import type { Body } from "./physics";

const e = (src: string, dst: string) => ({ src, dst, rel: "r" });
const nodes = ["me", "a", "b", "a1", "lone"].map((id) => ({ id }));
const edges = [e("me", "a"), e("b", "me"), e("a", "a1")];

describe("pickEgo", () => {
  it("takes the owner the server named", () => {
    expect(pickEgo(nodes, edges, "a1")).toBe("a1");
  });

  it("falls back to the best-connected node when the owner is not in the graph", () => {
    expect(pickEgo(nodes, edges, "nobody")).toBe("me");
    expect(pickEgo(nodes, edges, null)).toBe("me");
  });
});

describe("egoLayout", () => {
  const { ego, placements } = egoLayout(nodes, edges, "me");

  it("puts the ego at the origin", () => {
    expect(ego).toBe("me");
    expect(placements.get("me")).toMatchObject({ x: 0, y: 0, hop: 0 });
  });

  it("rings every node by its hop count, further out the further away", () => {
    expect(placements.get("a")!.hop).toBe(1);
    expect(placements.get("a1")!.hop).toBe(2);
    expect(placements.get("a1")!.ring).toBeGreaterThan(placements.get("a")!.ring);
  });

  it("gives an unreachable node the outermost ring rather than dropping it", () => {
    const lone = placements.get("lone")!;
    expect(lone.hop).toBe(3);
    expect(lone.ring).toBeGreaterThan(placements.get("a1")!.ring);
  });

  it("places each node on its ring", () => {
    for (const p of placements.values()) expect(Math.hypot(p.x, p.y)).toBeCloseTo(p.ring);
  });

  it("spreads a crowded ring over sub-rings so no two nodes overlap", () => {
    const many = Array.from({ length: 300 }, (_, i) => ({ id: `n${i}` }));
    const star = many.map((n) => e("hub", n.id));
    const out = egoLayout([{ id: "hub" }, ...many], star, "hub").placements;
    const pts = many.map((n) => out.get(n.id)!);
    let closest = Infinity;
    for (let i = 0; i < pts.length; i++)
      for (let j = i + 1; j < pts.length; j++)
        closest = Math.min(closest, Math.hypot(pts[i]!.x - pts[j]!.x, pts[i]!.y - pts[j]!.y));
    expect(closest).toBeGreaterThan(10);
  });
});

describe("applyLayout", () => {
  it("places new bodies but leaves existing ones where they are", () => {
    const bodies = new Map<string, Body>([["a", { id: "a", x: 999, y: 999, vx: 0, vy: 0 }]]);
    applyLayout(bodies, egoLayout(nodes, edges, "me"), false);
    expect(bodies.get("a")).toMatchObject({ x: 999, y: 999, hop: 1 });
    expect(bodies.has("a1")).toBe(true);
  });

  it("pins the ego at the origin", () => {
    const bodies = new Map<string, Body>();
    applyLayout(bodies, egoLayout(nodes, edges, "me"), false);
    expect(bodies.get("me")).toMatchObject({ x: 0, y: 0, fixed: true });
  });

  it("moves everything back to its placement and releases pins on a reset", () => {
    const bodies = new Map<string, Body>([["a", { id: "a", x: 999, y: 999, vx: 0, vy: 0, fixed: true }]]);
    const layout = egoLayout(nodes, edges, "me");
    applyLayout(bodies, layout, true);
    expect(bodies.get("a")).toMatchObject({ x: layout.placements.get("a")!.x, fixed: false });
  });
});
