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
  /** Hops from the ego, set by the ego layout. */
  hop?: number;
  /** The distance from the ego (the origin) the radial force holds it at. */
  ring?: number;
}

export const FORCES = {
  REPULSION: 900,
  /**
   * Repulsion only acts between bodies closer than this. It is what keeps
   * neighbours from overlapping; letting every node push every other node
   * across the whole map is what used to inflate and churn the layout, and it
   * cost N² per frame on top.
   */
  CUTOFF: 80,
  /**
   * Edge pull, divided by the busier end's degree: a hub with hundreds of edges
   * would otherwise drag its whole neighbourhood off the rings into a knot.
   */
  SPRING: 0.05,
  /** The rest length of an edge, in world units. */
  LENGTH: 60,
  /** Pull toward the body's ring around the ego. */
  RADIAL: 0.15,
  /** Pull toward the origin, for a body the ego layout has not placed. */
  GRAVITY: 0.002,
  DAMPING: 0.6,
  /** The furthest a body may move in one frame, so nothing ever visibly jumps. */
  MAX_STEP: 6,
  COOLING: 0.96,
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

  // Bucket bodies into cells one cutoff wide: only the 3x3 cells around a body
  // can hold anything close enough to push it.
  const cell = FORCES.CUTOFF;
  const grid = new Map<string, Body[]>();
  for (const b of vis) {
    const k = `${Math.floor(b.x / cell)},${Math.floor(b.y / cell)}`;
    (grid.get(k) ?? grid.set(k, []).get(k)!).push(b);
  }
  const cut2 = FORCES.CUTOFF * FORCES.CUTOFF;
  for (const a of vis) {
    const cx = Math.floor(a.x / cell);
    const cy = Math.floor(a.y / cell);
    for (let gx = cx - 1; gx <= cx + 1; gx++) {
      for (let gy = cy - 1; gy <= cy + 1; gy++) {
        for (const b of grid.get(`${gx},${gy}`) ?? []) {
          if (b === a) continue;
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 > cut2) continue;
          if (d2 < 0.01) {
            // Coincident bodies: separate them along a stable, id-derived axis.
            dx = a.id < b.id ? 1 : -1;
            dy = 0;
            d2 = 1;
          }
          const d = Math.sqrt(d2);
          // Each pair is visited from both sides, so each side moves only itself.
          const f = FORCES.REPULSION / Math.max(d2, 16);
          a.vx += (f * dx) / d;
          a.vy += (f * dy) / d;
        }
      }
    }
    const r = Math.hypot(a.x, a.y);
    if (a.ring !== undefined && a.ring > 0 && r > 0.01) {
      const f = FORCES.RADIAL * (a.ring - r);
      a.vx += (f * a.x) / r;
      a.vy += (f * a.y) / r;
    } else if (a.ring === undefined) {
      a.vx -= a.x * FORCES.GRAVITY;
      a.vy -= a.y * FORCES.GRAVITY;
    }
  }

  for (const e of edges) {
    const a = byId.get(e.src);
    const b = byId.get(e.dst);
    if (!a || !b) continue;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 0.01;
    const busier = Math.max(1, a.degree ?? 1, b.degree ?? 1);
    const f = (FORCES.SPRING / busier) * (d - FORCES.LENGTH);
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
    let mx = n.vx * alpha;
    let my = n.vy * alpha;
    const m = Math.hypot(mx, my);
    if (m > FORCES.MAX_STEP) {
      mx = (mx / m) * FORCES.MAX_STEP;
      my = (my / m) * FORCES.MAX_STEP;
    }
    n.x += mx;
    n.y += my;
    n.vx *= FORCES.DAMPING;
    n.vy *= FORCES.DAMPING;
  }

  return alpha * FORCES.COOLING;
}

/**
 * A node's dot grows with how connected it is, not with anything it says —
 * up to a cap, or a hub's dot swallows the ring of nodes around it.
 */
export function radiusOf(degree = 0): number {
  return 4 + Math.min(Math.sqrt(degree), 7) * 2.2;
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
