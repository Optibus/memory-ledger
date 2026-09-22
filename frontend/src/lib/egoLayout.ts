/**
 * The map's starting shape: the owner at the centre, everything else in rings
 * by how many hops it is from them.
 *
 * A random seed makes the simulation do all the work, and on a few thousand
 * nodes that work is a long, violent unknotting that ends in a hairball. Placing
 * every node where it belongs up front leaves the forces only small corrections
 * to make, so the map opens already readable and barely moves.
 */
import type { GraphEdge } from "@/api/types";
import type { Body } from "./physics";

export const RINGS = {
  /** Inner radius of the first ring around the ego, in world units. */
  FIRST: 140,
  /** Empty space between one hop's annulus and the next. */
  GAP: 90,
  /** How far apart two nodes sit, along a ring and between sub-rings. */
  SPACING: 24,
} as const;

export interface Placement {
  x: number;
  y: number;
  /** Hops from the ego; nodes it cannot reach share one outer ring. */
  hop: number;
  /** The distance from the ego the radial force holds this node at. */
  ring: number;
}

export interface Layout {
  ego: string | null;
  placements: Map<string, Placement>;
}

interface LayoutNode {
  id: string;
  type?: string;
}

/**
 * Picks the centre: the owner if the server named one that is in the graph,
 * otherwise the best-connected node, so an unset owner still gets a hub.
 */
export function pickEgo(nodes: LayoutNode[], edges: GraphEdge[], ego?: string | null): string | null {
  if (ego && nodes.some((n) => n.id === ego)) return ego;
  const degree = new Map<string, number>();
  for (const e of edges) {
    degree.set(e.src, (degree.get(e.src) ?? 0) + 1);
    degree.set(e.dst, (degree.get(e.dst) ?? 0) + 1);
  }
  let best: string | null = null;
  let most = -1;
  for (const n of nodes) {
    const d = degree.get(n.id) ?? 0;
    if (d > most) {
      most = d;
      best = n.id;
    }
  }
  return best;
}

export function egoLayout(nodes: LayoutNode[], edges: GraphEdge[], wanted?: string | null): Layout {
  const placements = new Map<string, Placement>();
  const ego = pickEgo(nodes, edges, wanted);
  if (!ego) return { ego, placements };

  const ids = new Set(nodes.map((n) => n.id));
  const typeOf = new Map(nodes.map((n) => [n.id, n.type ?? ""]));
  const adj = new Map<string, string[]>();
  for (const e of edges) {
    if (!ids.has(e.src) || !ids.has(e.dst) || e.src === e.dst) continue;
    (adj.get(e.src) ?? adj.set(e.src, []).get(e.src)!).push(e.dst);
    (adj.get(e.dst) ?? adj.set(e.dst, []).get(e.dst)!).push(e.src);
  }

  // Breadth-first from the ego. Each node remembers who reached it first, so it
  // can sit at the same angle as its parent and a branch stays together.
  const hop = new Map<string, number>([[ego, 0]]);
  const parent = new Map<string, string>();
  const levels: string[][] = [[ego]];
  for (let k = 0; levels[k]!.length; k++) {
    const next: string[] = [];
    for (const id of levels[k]!) {
      for (const nb of adj.get(id) ?? []) {
        if (hop.has(nb)) continue;
        hop.set(nb, k + 1);
        parent.set(nb, id);
        next.push(nb);
      }
    }
    levels.push(next);
  }
  levels.pop();
  const stray = nodes.map((n) => n.id).filter((id) => !hop.has(id));
  if (stray.length) {
    for (const id of stray) hop.set(id, levels.length);
    levels.push(stray);
  }

  placements.set(ego, { x: 0, y: 0, hop: 0, ring: 0 });
  const angle = new Map<string, number>([[ego, 0]]);
  let inner = RINGS.FIRST;
  for (let k = 1; k < levels.length; k++) {
    const level = levels[k]!;
    // Order around the ring by the parent's angle, then by type, so children
    // land beside their parent and like sits with like.
    const key = (id: string) => angle.get(parent.get(id) ?? "") ?? Infinity;
    level.sort(
      (a, b) => key(a) - key(b) || (typeOf.get(a) ?? "").localeCompare(typeOf.get(b) ?? "") || a.localeCompare(b),
    );
    // A ring too crowded for one circle becomes an annulus of sub-rings. Nodes
    // alternate between them in angular order, so neighbours never overlap.
    const count = level.length;
    const subs = Math.max(1, Math.ceil((count * RINGS.SPACING) / (2 * Math.PI * inner)));
    // Angles are cut for a whole number of nodes per sub-ring, or the last few
    // wrap round onto the first few of the same sub-ring.
    const slots = Math.ceil(count / subs) * subs;
    level.forEach((id, i) => {
      const theta = (2 * Math.PI * i) / slots;
      const r = inner + (i % subs) * RINGS.SPACING;
      angle.set(id, theta);
      placements.set(id, { x: Math.cos(theta) * r, y: Math.sin(theta) * r, hop: k, ring: r });
    });
    inner += (subs - 1) * RINGS.SPACING + RINGS.GAP;
  }
  return { ego, placements };
}

/**
 * Writes a layout onto the bodies. With `reposition` every body jumps to its
 * placement (first load, or an explicit reset); without it only new bodies
 * are placed and the existing ones keep their position and just get new
 * targets, so a poll that adds one node does not rearrange the map.
 */
export function applyLayout(bodies: Map<string, Body>, layout: Layout, reposition: boolean): void {
  for (const [id, p] of layout.placements) {
    let b = bodies.get(id);
    if (!b) {
      b = { id, x: p.x, y: p.y, vx: 0, vy: 0 };
      bodies.set(id, b);
    } else if (reposition) {
      b.x = p.x;
      b.y = p.y;
      b.vx = 0;
      b.vy = 0;
      b.fixed = false;
    }
    b.hop = p.hop;
    b.ring = p.ring;
    if (id === layout.ego) {
      // The ego is the fixed point the rest is arranged around.
      b.x = 0;
      b.y = 0;
      b.fixed = true;
    }
  }
}
