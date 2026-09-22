/**
 * Which nodes the map's filters drop, and the knock-on effect of dropping them.
 *
 * This is deliberately pure and free of React and the canvas: it is the part
 * with actual rules in it (`docs/api.md` documents the behaviour), so it is the
 * part worth testing directly.
 */
import type { GraphEdge, GraphNode } from "@/api/types";
import { DONE_STATUSES } from "./vocab";

export interface FilterState {
  /** Values switched OFF. Absent means visible — a realm invented today shows up. */
  typesOff: Set<string>;
  realmsOff: Set<string>;
  kindsOff: Set<string>;
  hideDone: boolean;
  /** Drop a node once nothing visible points at it any more. */
  cascade: boolean;
  /** What falling out LOOKS like: dimmed in place instead of removed. */
  shade: boolean;
}

export function emptyFilter(): FilterState {
  return {
    typesOff: new Set(),
    realmsOff: new Set(),
    kindsOff: new Set(),
    hideDone: false,
    cascade: false,
    shade: false,
  };
}

/** What the chips and toggles drop directly, before any knock-on effect. */
export function directHidden(node: GraphNode, f: FilterState): boolean {
  if (f.typesOff.has(node.type)) return true;
  if (f.realmsOff.has(node.realm || "")) return true;
  if (f.kindsOff.has(node.kind || "")) return true;
  if (f.hideDone && node.status && DONE_STATUSES.has(node.status)) return true;
  return false;
}

/**
 * "drop unsupported": a node survives while at least one edge still points at
 * it from something that survived. A node nothing points at is a root, and only
 * the chips can drop it.
 *
 *   A -> B -> C -> D,  E -> C
 *   hide A        B goes with it; C survives on E, so D does too
 *   hide A and E  B, then C, then D — the whole chain unwinds
 *
 * Runs to a fixpoint because order matters: C has to fall before D can. Cycles
 * hold each other up and are never cascaded away on their own; a self-loop is
 * not support.
 */
export function computeCascade(
  nodes: GraphNode[],
  edges: GraphEdge[],
  f: FilterState,
): Set<string> {
  const off = new Set<string>();
  if (!f.cascade) return off;

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const parents = new Map<string, string[]>();
  for (const e of edges) {
    if (e.src === e.dst) continue; // a self-loop is not support
    const list = parents.get(e.dst);
    if (list) list.push(e.src);
    else parents.set(e.dst, [e.src]);
  }

  const gone = (id: string): boolean => {
    const n = byId.get(id);
    return !n || directHidden(n, f) || off.has(id);
  };

  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const n of nodes) {
      if (off.has(n.id) || directHidden(n, f)) continue;
      const src = parents.get(n.id);
      if (!src || !src.length) continue; // root: nothing to lose
      if (src.every(gone)) {
        off.add(n.id);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return off;
}

/**
 * One predicate feeds both paint modes, which is what keeps them honest: every
 * filter computes this and nothing else, so flipping `shade` can never change
 * WHICH nodes are affected, only how they are painted.
 */
export function filteredOut(node: GraphNode, f: FilterState, cascadeOff: Set<string>): boolean {
  return directHidden(node, f) || cascadeOff.has(node.id);
}

export function isHidden(node: GraphNode, f: FilterState, cascadeOff: Set<string>): boolean {
  return !f.shade && filteredOut(node, f, cascadeOff);
}

export function isShaded(node: GraphNode, f: FilterState, cascadeOff: Set<string>): boolean {
  return f.shade && filteredOut(node, f, cascadeOff);
}

/** Search matches title, type or id — the three things you know a node by. */
export function matchesQuery(node: GraphNode, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    (node.title || "").toLowerCase().includes(q) ||
    node.type.toLowerCase().includes(q) ||
    node.id.toLowerCase().includes(q)
  );
}

/**
 * The filter as something JSON can hold.
 *
 * `Set` does not survive `JSON.stringify` — it serialises as `{}` — so a filter
 * stored naively comes back with every facet switched on and looks like the
 * preference simply failed to save. These two functions are the only place that
 * conversion happens.
 */
export interface StoredFilter {
  typesOff: string[];
  realmsOff: string[];
  kindsOff: string[];
  hideDone: boolean;
  cascade: boolean;
  shade: boolean;
}

export function serializeFilter(f: FilterState): StoredFilter {
  return {
    typesOff: [...f.typesOff].sort(),
    realmsOff: [...f.realmsOff].sort(),
    kindsOff: [...f.kindsOff].sort(),
    hideDone: f.hideDone,
    cascade: f.cascade,
    shade: f.shade,
  };
}

/** Tolerant on purpose: a hand-edited or half-written blob must degrade to the
 *  empty filter, never to a map with nodes missing for no visible reason. */
export function deserializeFilter(raw: unknown): FilterState {
  const f = emptyFilter();
  if (!raw || typeof raw !== "object") return f;
  const s = raw as Partial<StoredFilter>;
  if (Array.isArray(s.typesOff)) f.typesOff = new Set(s.typesOff.filter(isStr));
  if (Array.isArray(s.realmsOff)) f.realmsOff = new Set(s.realmsOff.filter(isStr));
  if (Array.isArray(s.kindsOff)) f.kindsOff = new Set(s.kindsOff.filter(isStr));
  f.hideDone = s.hideDone === true;
  f.cascade = s.cascade === true;
  f.shade = s.shade === true;
  return f;
}

function isStr(v: unknown): v is string {
  return typeof v === "string";
}
