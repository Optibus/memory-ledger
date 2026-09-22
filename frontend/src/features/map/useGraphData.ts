import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/api/client";
import { usePolling } from "@/hooks/usePolling";
import type { GraphEdge, GraphNode, GraphPayload } from "@/api/types";
import type { Body } from "@/lib/physics";

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  byId: Map<string, GraphNode>;
  bodies: Map<string, Body>;
  meta: GraphPayload["meta"];
  live: boolean;
  error: string | null;
  /** Bumped whenever the node or edge SET changed, so the layout can reheat. */
  version: number;
  refresh: () => void;
}

/**
 * The graph, polled, with positions that survive a poll.
 *
 * A node keeps the body it already has; only a genuinely new node gets a seed
 * position. Rebuilding the bodies each poll is what made the old map jump every
 * two seconds on a brain that was being written to.
 */
export function useGraphData(pollMs: number, enabled: boolean): GraphData {
  const { data, error, live, refresh } = usePolling<GraphPayload>(
    (signal) => api.graph(signal),
    { intervalMs: pollMs, enabled },
  );

  const bodies = useRef(new Map<string, Body>());
  const [version, setVersion] = useState(0);
  const lastShape = useRef("");

  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const edges = useMemo(() => data?.edges ?? [], [data]);

  useEffect(() => {
    let changed = false;
    const seen = new Set<string>();
    for (const n of nodes) {
      seen.add(n.id);
      const body = bodies.current.get(n.id);
      if (body) {
        body.degree = n.degree ?? 0;
        continue;
      }
      bodies.current.set(n.id, {
        id: n.id,
        x: (Math.random() - 0.5) * 300,
        y: (Math.random() - 0.5) * 300,
        vx: 0,
        vy: 0,
        degree: n.degree ?? 0,
      });
      changed = true;
    }
    for (const id of [...bodies.current.keys()]) {
      if (!seen.has(id)) {
        bodies.current.delete(id);
        changed = true;
      }
    }
    // The edge set changing moves the layout even when no node did.
    const shape = `${nodes.length}:${edges.length}`;
    if (shape !== lastShape.current) {
      lastShape.current = shape;
      changed = true;
    }
    if (changed) setVersion((v) => v + 1);
  }, [nodes, edges]);

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  return {
    nodes,
    edges,
    byId,
    bodies: bodies.current,
    meta: data?.meta ?? {},
    live,
    error,
    version,
    refresh,
  };
}
