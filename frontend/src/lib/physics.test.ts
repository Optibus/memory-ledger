import { describe, expect, it } from "vitest";
import { FORCES, neighborsOf, radiusOf, step, toScreen, toWorld } from "./physics";
import type { Body } from "./physics";

const body = (id: string, x: number, y: number): Body => ({ id, x, y, vx: 0, vy: 0 });

describe("step", () => {
  it("does nothing once the layout has cooled below the floor", () => {
    const bodies = [body("a", 10, 0), body("b", -10, 0)];
    const alpha = step({ bodies, edges: [], alpha: FORCES.FLOOR / 2 });
    expect(alpha).toBe(FORCES.FLOOR / 2);
    expect(bodies[0]!.x).toBe(10);
  });

  it("cools the layout every frame it runs", () => {
    const next = step({ bodies: [body("a", 1, 1)], edges: [], alpha: 1 });
    expect(next).toBeCloseTo(FORCES.COOLING);
  });

  it("pushes two overlapping nodes apart", () => {
    const bodies = [body("a", 1, 0), body("b", -1, 0)];
    step({ bodies, edges: [], alpha: 1 });
    expect(bodies[0]!.x).toBeGreaterThan(1);
    expect(bodies[1]!.x).toBeLessThan(-1);
  });

  it("pulls an over-stretched edge back toward its rest length", () => {
    const bodies = [body("a", 0, 0), body("b", 600, 0)];
    const before = bodies[1]!.x - bodies[0]!.x;
    step({ bodies, edges: [{ src: "a", dst: "b", rel: "relates-to" }], alpha: 1 });
    expect(bodies[1]!.x - bodies[0]!.x).toBeLessThan(before);
  });

  it("holds a fixed body exactly where it is, and zeroes its velocity", () => {
    const pinned: Body = { id: "a", x: 50, y: 50, vx: 9, vy: 9, fixed: true };
    step({ bodies: [pinned, body("b", 0, 0)], edges: [], alpha: 1 });
    expect(pinned.x).toBe(50);
    expect(pinned.y).toBe(50);
    expect(pinned.vx).toBe(0);
  });

  it("holds the body being dragged, named by id rather than by flag", () => {
    const dragged = body("a", 5, 5);
    step({ bodies: [dragged, body("b", 0, 0)], edges: [], alpha: 1, pinned: "a" });
    expect(dragged.x).toBe(5);
  });

  it("leaves hidden bodies out of the simulation entirely", () => {
    const gone = body("gone", 1, 0);
    const kept = body("kept", -1, 0);
    step({ bodies: [gone, kept], edges: [], alpha: 1, hidden: new Set(["gone"]) });
    expect(gone.x).toBe(1);
    expect(gone.vx).toBe(0);
    // kept still felt gravity, so it did move
    expect(kept.x).not.toBe(-1);
  });

  it("ignores an edge whose endpoint is hidden", () => {
    const a = body("a", 0, 0);
    const b = body("b", 600, 0);
    step({
      bodies: [a, b],
      edges: [{ src: "a", dst: "b", rel: "relates-to" }],
      alpha: 1,
      hidden: new Set(["b"]),
    });
    // Only gravity acted on `a`; the spring would have pulled it far further right.
    expect(a.x).toBeCloseTo(0, 5);
  });
});

describe("radiusOf", () => {
  it("grows with degree and never collapses to nothing", () => {
    expect(radiusOf(0)).toBe(4);
    expect(radiusOf(9)).toBeCloseTo(4 + 3 * 2.2);
    expect(radiusOf()).toBe(4);
  });
});

describe("the viewport transform", () => {
  it("round-trips a point through screen space", () => {
    const v = { scale: 2.5, tx: 100, ty: -40 };
    const p = { x: 17, y: -9 };
    const s = toScreen(p, v);
    const back = toWorld(s.x, s.y, v);
    expect(back.x).toBeCloseTo(p.x);
    expect(back.y).toBeCloseTo(p.y);
  });
});

describe("neighborsOf", () => {
  it("collects both ends of every touching edge", () => {
    const edges = [
      { src: "a", dst: "b", rel: "r" },
      { src: "c", dst: "a", rel: "r" },
      { src: "d", dst: "e", rel: "r" },
    ];
    expect([...neighborsOf("a", edges)].sort()).toEqual(["b", "c"]);
  });

  it("returns nothing for a node with no edges", () => {
    expect(neighborsOf("z", []).size).toBe(0);
  });
});
