import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/api/client";
import { usePolling } from "@/hooks/usePolling";
import type { GraphEdge, GraphNode, GraphPayload } from "@/api/types";
import { applyLayout, egoLayout } from "@/lib/egoLayout";
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
  /** The node the layout is centred on: the owner, else the best-connected node. */
  ego: string | null;
  refresh: () => void;
}

/**
 * The graph, polled, with positions that survive a poll.
 *
 * The first load lays the graph out around the ego (see egoLayout). After that
 * a node keeps the body it already has: a poll that changes the graph's shape
 * only places the new nodes and retargets the rings, so the map you are reading
 * does not rearrange itself under the cursor.
 */
export function useGraphData(pollMs: number, enabled: boolean): GraphData {
  const { data, error, live, refresh } = usePolling<GraphPayload>(
    (signal) => api.graph(signal),
    { intervalMs: pollMs, enabled },
  );

  const bodies = useRef(new Map<string, Body>());
  const [version, setVersion] = useState(0);
  const [ego, setEgo] = useState<string | null>(null);
  const lastShape = useRef("");

  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const edges = useMemo(() => data?.edges ?? [], [data]);
  const wantedEgo = data?.meta?.ego ?? null;

  useEffect(() => {
    const byBody = bodies.current;
    for (const n of nodes) {
      const body = byBody.get(n.id);
      if (body) body.degree = n.degree ?? 0;
    }
    // The edge set changing moves the layout even when no node did.
    const shape = `${nodes.length}:${edges.length}:${wantedEgo ?? ""}`;
    if (shape === lastShape.current) return;
    lastShape.current = shape;

    const seen = new Set(nodes.map((n) => n.id));
    for (const id of [...byBody.keys()]) if (!seen.has(id)) byBody.delete(id);
    const layout = egoLayout(nodes, edges, wantedEgo);
    applyLayout(byBody, layout, byBody.size === 0);
    for (const n of nodes) {
      const body = byBody.get(n.id);
      if (body) body.degree = n.degree ?? 0;
    }
    setEgo(layout.ego);
    setVersion((v) => v + 1);
  }, [nodes, edges, wantedEgo]);

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
    ego,
    refresh,
  };
}
