/**
 * The force simulation behind the map, as a pure step over a mutable body list.
 *
 * Keeping it out of the component means the layout can be tested without a
 * canvas, a DOM or an animation frame — and the canvas layer reduces to "draw
 * these positions".
 */
import type { GraphEdge } from "@/api/types";

export interface Body {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  /** Dragged or pinned bodies hold their position and take no velocity. */
  fixed?: boolean;
  degree?: number;
}

export const FORCES = {
  REPULSION: 1600,
  SPRING: 0.02,
  /** The rest length of an edge, in world units. */
  LENGTH: 70,
  GRAVITY: 0.015,
  DAMPING: 0.82,
  COOLING: 0.985,
  /** Below this the layout has settled and stepping it again is wasted work. */
  FLOOR: 0.02,
} as const;

export interface StepOptions {
  bodies: Body[];
  edges: GraphEdge[];
  alpha: number;
  /** Ids the filters removed — they take no forces and exert none. */
  hidden?: Set<string>;
  pinned?: string | null;
}

/** Advances the layout one frame and returns the cooled alpha. */
export function step({ bodies, edges, alpha, hidden, pinned }: StepOptions): number {
  if (alpha < FORCES.FLOOR) return alpha;
  const skip = hidden ?? new Set<string>();
  const vis = bodies.filter((b) => !skip.has(b.id));
  const byId = new Map(vis.map((b) => [b.id, b]));

  for (let i = 0; i < vis.length; i++) {
    const a = vis[i]!;
    for (let j = i + 1; j < vis.length; j++) {
      const b = vis[j]!;
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = dx * dx + dy * dy || 0.01;
      const d = Math.sqrt(d2);
      const f = FORCES.REPULSION / d2;
      const fx = (f * dx) / d;
      const fy = (f * dy) / d;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    }
    a.vx -= a.x * FORCES.GRAVITY; // gravity toward the centre
    a.vy -= a.y * FORCES.GRAVITY;
  }

  for (const e of edges) {
    const a = byId.get(e.src);
    const b = byId.get(e.dst);
    if (!a || !b) continue;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 0.01;
    const f = FORCES.SPRING * (d - FORCES.LENGTH);
    const fx = (f * dx) / d;
    const fy = (f * dy) / d;
    a.vx += fx;
    a.vy += fy;
    b.vx -= fx;
    b.vy -= fy;
  }

  for (const n of vis) {
    if (n.fixed || n.id === pinned) {
      n.vx = 0;
      n.vy = 0;
      continue;
    }
    n.x += n.vx * alpha;
    n.y += n.vy * alpha;
    n.vx *= FORCES.DAMPING;
    n.vy *= FORCES.DAMPING;
  }

  return alpha * FORCES.COOLING;
}

/** A node's dot grows with how connected it is, not with anything it says. */
export function radiusOf(degree = 0): number {
  return 4 + Math.sqrt(degree) * 2.2;
}

export interface Viewport {
  scale: number;
  tx: number;
  ty: number;
}

export function toScreen(p: { x: number; y: number }, v: Viewport) {
  return { x: p.x * v.scale + v.tx, y: p.y * v.scale + v.ty };
}

export function toWorld(sx: number, sy: number, v: Viewport) {
  return { x: (sx - v.tx) / v.scale, y: (sy - v.ty) / v.scale };
}

/** The set of ids on the other end of any edge touching `id`. */
export function neighborsOf(id: string, edges: GraphEdge[]): Set<string> {
  const set = new Set<string>();
  for (const e of edges) {
    if (e.src === id) set.add(e.dst);
    if (e.dst === id) set.add(e.src);
  }
  return set;
}
