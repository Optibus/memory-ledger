import { useCallback, useEffect, useRef } from "react";
import type { GraphEdge, GraphNode } from "@/api/types";
import { colorForType } from "@/lib/colors";
import { neighborsOf, radiusOf, step, toScreen, toWorld } from "@/lib/physics";
import type { Body, Viewport } from "@/lib/physics";
import { isHidden, isShaded, matchesQuery } from "@/lib/graphFilter";
import type { FilterState } from "@/lib/graphFilter";
import "./GraphCanvas.css";

export interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  bodies: Map<string, Body>;
  filter: FilterState;
  cascadeOff: Set<string>;
  query: string;
  selected: string | null;
  onSelect: (id: string | null) => void;
  /** Bumped when the graph changed shape, so the layout reheats. */
  version: number;
  /** Set by the parent to recentre on a node the card linked to. */
  focusId?: string | null;
}

const LABEL_SCALE = 0.75;

/**
 * The force-directed map.
 *
 * React owns none of the per-frame work: the simulation and the paint run on
 * one `requestAnimationFrame` loop against refs, and the component re-renders
 * only when the data or the filters change. Re-rendering 800 nodes through the
 * virtual DOM sixty times a second is the one thing a canvas exists to avoid.
 */
export function GraphCanvas({
  nodes,
  edges,
  bodies,
  filter,
  cascadeOff,
  query,
  selected,
  onSelect,
  version,
  focusId,
}: GraphCanvasProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const view = useRef<Viewport>({ scale: 1, tx: 0, ty: 0 });
  // Whether the viewport has ever been measured at a real size. The canvas can
  // be laid out at 0x0 on the first effect (a hidden route, a font still
  // loading), and centring on a width of zero leaves the whole graph pinned to
  // the top-left corner with nothing to suggest why.
  const centred = useRef(false);
  const alpha = useRef(1);
  const hover = useRef<string | null>(null);
  const drag = useRef<{ id: string | null; panning: boolean; x: number; y: number; moved: boolean }>({
    id: null,
    panning: false,
    x: 0,
    y: 0,
    moved: false,
  });

  // Live mirrors of the props the render loop reads, so the loop is started once
  // and never torn down and rebuilt on a filter change.
  const state = useRef({ nodes, edges, filter, cascadeOff, query, selected });
  state.current = { nodes, edges, filter, cascadeOff, query, selected };

  useEffect(() => {
    alpha.current = Math.max(alpha.current, 0.6);
  }, [version]);

  useEffect(() => {
    alpha.current = Math.max(alpha.current, 0.3);
  }, [filter, cascadeOff]);

  // Centre on a node the detail card asked for, without disturbing the zoom.
  useEffect(() => {
    if (!focusId) return;
    const body = bodies.get(focusId);
    const cv = canvas.current;
    if (!body || !cv) return;
    const v = view.current;
    v.tx = cv.clientWidth / 2 - body.x * v.scale;
    v.ty = cv.clientHeight / 2 - body.y * v.scale;
  }, [focusId, bodies]);

  const hiddenIds = useCallback(() => {
    const { nodes: ns, filter: f, cascadeOff: off } = state.current;
    const set = new Set<string>();
    for (const n of ns) if (isHidden(n, f, off)) set.add(n.id);
    return set;
  }, []);

  const nodeAt = useCallback(
    (sx: number, sy: number): GraphNode | null => {
      const { nodes: ns, filter: f, cascadeOff: off } = state.current;
      const v = view.current;
      for (let i = ns.length - 1; i >= 0; i--) {
        const n = ns[i]!;
        if (isHidden(n, f, off)) continue;
        const body = bodies.get(n.id);
        if (!body) continue;
        const p = toScreen(body, v);
        const r = radiusOf(n.degree) * Math.max(0.6, Math.min(v.scale, 2)) + 3;
        if ((sx - p.x) ** 2 + (sy - p.y) ** 2 <= r * r) return n;
      }
      return null;
    },
    [bodies],
  );

  // ---- the render loop -----------------------------------------------------
  useEffect(() => {
    const cv = canvas.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    const css = (name: string) =>
      getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const palette = { fg: css("--fg") || "#e6edf3", hi: css("--edge-hi") || "#58a6ff" };

    const resize = () => {
      const dpr = Math.max(1, window.devicePixelRatio || 1);
      const w = cv.clientWidth;
      const h = cv.clientHeight;
      // Writing width/height resets the context, so only do it when the size
      // actually changed. Unconditional writes under a ResizeObserver are how a
      // measure-then-resize pair turns into a feedback loop.
      if (cv.width !== w * dpr || cv.height !== h * dpr) {
        cv.width = w * dpr;
        cv.height = h * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      if (!centred.current && w > 0 && h > 0) {
        view.current.tx = w / 2;
        view.current.ty = h / 2;
        centred.current = true;
      }
    };
    resize();
    window.addEventListener("resize", resize);
    // A ResizeObserver catches the case a single measurement cannot: the canvas
    // gaining its size after this effect ran.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    ro?.observe(cv);

    const draw = () => {
      const { nodes: ns, edges: es, filter: f, cascadeOff: off, query: q, selected: sel } =
        state.current;
      const v = view.current;
      const hiddenSet = new Set<string>();
      for (const n of ns) if (isHidden(n, f, off)) hiddenSet.add(n.id);

      alpha.current = step({
        bodies: [...bodies.values()],
        edges: es,
        alpha: alpha.current,
        hidden: hiddenSet,
        pinned: drag.current.id,
      });

      ctx.clearRect(0, 0, cv.clientWidth, cv.clientHeight);
      const nodeById = new Map(ns.map((n) => [n.id, n]));
      const focus = sel || hover.current;
      const focusN = focus ? neighborsOf(focus, es) : null;

      // edges
      ctx.lineWidth = 1;
      for (const e of es) {
        const a = nodeById.get(e.src);
        const b = nodeById.get(e.dst);
        if (!a || !b || hiddenSet.has(a.id) || hiddenSet.has(b.id)) continue;
        const ba = bodies.get(a.id);
        const bb = bodies.get(b.id);
        if (!ba || !bb) continue;
        const inFocus = !!focus && (e.src === focus || e.dst === focus);
        // An edge is only as lit as its dimmer end, or a shaded node would stay
        // wired into the lit graph by full-strength lines and read as part of it.
        const edgeShaded = isShaded(a, f, off) || isShaded(b, f, off);
        const A = toScreen(ba, v);
        const B = toScreen(bb, v);
        ctx.strokeStyle = inFocus ? palette.hi : focus ? "#2a2f36" : "#3a4048";
        ctx.globalAlpha = (focus && !inFocus) || edgeShaded ? 0.25 : inFocus ? 0.9 : 0.5;
        ctx.beginPath();
        ctx.moveTo(A.x, A.y);
        ctx.lineTo(B.x, B.y);
        ctx.stroke();
        if (inFocus) arrow(ctx, A, B, radiusOf(b.degree), palette.hi);
      }
      ctx.globalAlpha = 1;

      // nodes
      const showLabels = v.scale > LABEL_SCALE;
      for (const n of ns) {
        if (hiddenSet.has(n.id)) continue;
        const body = bodies.get(n.id);
        if (!body) continue;
        const p = toScreen(body, v);
        const r = radiusOf(n.degree) * Math.max(0.6, Math.min(v.scale, 2));
        const m = matchesQuery(n, q);
        // A search hit stays lit even when shaded: you asked for it by name, and
        // dimming it would read as "no results" rather than "filtered out".
        const dim =
          (!!focus && focus !== n.id && !focusN?.has(n.id)) ||
          (!!q && !m) ||
          (isShaded(n, f, off) && !(q && m));
        ctx.globalAlpha = dim ? 0.2 : 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = colorForType(n.type);
        ctx.fill();
        if (n.id === sel) {
          ctx.lineWidth = 3;
          ctx.strokeStyle = "#fff";
          ctx.stroke();
        } else if (q && m) {
          ctx.lineWidth = 2;
          ctx.strokeStyle = "#fff";
          ctx.stroke();
        }
        // A shaded node keeps its dot and its edges — the shape it was kept for —
        // but not its label: a few thousand dimmed labels over the handful you
        // isolated is noise. Selected, hovered and search hits keep theirs.
        const named = n.id === sel || n.id === hover.current || (!!q && m && !dim);
        if (named || (showLabels && !isShaded(n, f, off))) {
          ctx.globalAlpha = dim ? 0.3 : 1;
          ctx.fillStyle = palette.fg;
          ctx.font = "11px ui-sans-serif";
          ctx.fillText((n.title || n.id).slice(0, 28), p.x + r + 3, p.y + 3);
        }
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      ro?.disconnect();
    };
  }, [bodies]);

  // ---- interaction ---------------------------------------------------------
  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const n = nodeAt(e.clientX, e.clientY);
    drag.current = {
      id: n?.id ?? null,
      panning: !n,
      x: e.clientX,
      y: e.clientY,
      moved: false,
    };
  };

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (Math.abs(e.clientX - d.x) > 4 || Math.abs(e.clientY - d.y) > 4) d.moved = true;

    if (d.id) {
      const body = bodies.get(d.id);
      if (body) {
        const w = toWorld(e.clientX, e.clientY, view.current);
        body.x = w.x;
        body.y = w.y;
        body.vx = 0;
        body.vy = 0;
        alpha.current = Math.max(alpha.current, 0.3);
      }
      return;
    }
    if (d.panning) {
      view.current.tx += e.movementX;
      view.current.ty += e.movementY;
      return;
    }
    hover.current = nodeAt(e.clientX, e.clientY)?.id ?? null;
  };

  const onMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (d.id && d.moved) {
      // Dragging a node PINS it — that is how you hold a cluster still.
      const body = bodies.get(d.id);
      if (body) body.fixed = true;
    } else if (!d.moved) {
      const n = nodeAt(e.clientX, e.clientY);
      onSelect(n?.id ?? null);
    }
    drag.current = { id: null, panning: false, x: 0, y: 0, moved: false };
  };

  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    const v = view.current;
    const factor = Math.exp(-e.deltaY * 0.0015);
    const next = Math.max(0.15, Math.min(6, v.scale * factor));
    // Zoom toward the cursor, not the origin.
    const w = toWorld(e.clientX, e.clientY, v);
    v.scale = next;
    v.tx = e.clientX - w.x * next;
    v.ty = e.clientY - w.y * next;
  };

  const onDoubleClick = () => {
    const cv = canvas.current;
    if (!cv) return;
    view.current = { scale: 1, tx: cv.clientWidth / 2, ty: cv.clientHeight / 2 };
    centred.current = true;
    for (const b of bodies.values()) b.fixed = false;
    alpha.current = 1;
    onSelect(null);
  };

  return (
    <canvas
      ref={canvas}
      className={`ml-canvas${drag.current.panning ? " is-grabbing" : ""}`}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={() => {
        hover.current = null;
        drag.current = { id: null, panning: false, x: 0, y: 0, moved: false };
      }}
      onWheel={onWheel}
      onDoubleClick={onDoubleClick}
      aria-label={`graph of ${nodes.length} nodes and ${edges.length} edges`}
      role="img"
      data-hidden-count={hiddenIds().size}
    />
  );
}

function arrow(
  ctx: CanvasRenderingContext2D,
  A: { x: number; y: number },
  B: { x: number; y: number },
  rB: number,
  color: string,
) {
  const ang = Math.atan2(B.y - A.y, B.x - A.x);
  const x = B.x - Math.cos(ang) * (rB + 2);
  const y = B.y - Math.sin(ang) * (rB + 2);
  const s = 6;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - Math.cos(ang - 0.4) * s, y - Math.sin(ang - 0.4) * s);
  ctx.lineTo(x - Math.cos(ang + 0.4) * s, y - Math.sin(ang + 0.4) * s);
  ctx.closePath();
  ctx.fill();
}
