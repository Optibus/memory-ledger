import { useMemo, useState } from "react";
import { useBootReload } from "@/api/MetaContext";
import { useIntervals } from "@/hooks/useIntervals";
import { Panel, StatusMessage } from "@/components";
import type { FacetOption } from "@/components";
import { colorForType, realmPalette } from "@/lib/colors";
import { computeCascade, emptyFilter, filteredOut } from "@/lib/graphFilter";
import type { FilterState } from "@/lib/graphFilter";
import { GraphCanvas } from "./GraphCanvas";
import { MapToolbar } from "./MapToolbar";
import { NodeCard } from "./NodeCard";
import { useGraphData } from "./useGraphData";
import "./MapSection.css";

/** The map: the whole brain as one force-directed graph you can read and edit. */
export function MapSection() {
  const [filter, setFilter] = useState<FilterState>(emptyFilter);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);

  const { mapPollMs } = useIntervals();
  const graph = useGraphData(mapPollMs, true);
  useBootReload(graph.meta.boot);

  const cascadeOff = useMemo(
    () => computeCascade(graph.nodes, graph.edges, filter),
    [graph.nodes, graph.edges, filter],
  );

  const visible = useMemo(
    () => graph.nodes.filter((n) => !filteredOut(n, filter, cascadeOff)),
    [graph.nodes, filter, cascadeOff],
  );

  // The facet options and their counts come from the payload, never from a list
  // in this file — a realm invented today is a filter on the next poll.
  const typeOptions: FacetOption[] = useMemo(
    () =>
      Object.entries(graph.meta.by_type ?? {})
        .sort((a, b) => b[1] - a[1])
        .map(([type, count]) => ({ value: type, count, swatch: colorForType(type) })),
    [graph.meta.by_type],
  );

  const realmOptions: FacetOption[] = useMemo(() => {
    const keys = Object.keys(graph.meta.by_realm ?? {}).sort();
    const paint = realmPalette(keys);
    return keys.map((realm) => ({
      value: realm,
      label: realm || "(none)",
      count: graph.meta.by_realm?.[realm],
      swatch: paint(realm),
    }));
  }, [graph.meta.by_realm]);

  const kindOptions: FacetOption[] = useMemo(
    () =>
      Object.entries(graph.meta.by_kind ?? {})
        .sort((a, b) => b[1] - a[1])
        .map(([kind, count]) => ({ value: kind, label: kind || "(none)", count })),
    [graph.meta.by_kind],
  );

  const selectedNode = selected ? graph.byId.get(selected) ?? null : null;

  const stats = `${visible.length}/${graph.nodes.length} nodes · ${graph.edges.length} edges`;

  return (
    <>
      <GraphCanvas
        nodes={graph.nodes}
        edges={graph.edges}
        bodies={graph.bodies}
        filter={filter}
        cascadeOff={cascadeOff}
        query={query}
        selected={selected}
        onSelect={setSelected}
        version={graph.version}
        focusId={focusId}
      />
      <MapToolbar
        query={query}
        onQuery={setQuery}
        filter={filter}
        onFilter={setFilter}
        typeOptions={typeOptions}
        realmOptions={realmOptions}
        kindOptions={kindOptions}
        cascadeCount={cascadeOff.size}
        stats={stats}
        live={graph.live}
      />
      {selectedNode ? (
        <NodeCard
          node={selectedNode}
          edges={graph.edges}
          byId={graph.byId}
          onClose={() => setSelected(null)}
          onFocus={(id) => {
            setSelected(id);
            setFocusId(id);
          }}
          onChanged={graph.refresh}
        />
      ) : null}
      <Panel className="ml-map__hint">
        scroll = zoom · drag bg = pan · drag node = pin · click = inspect · dbl-click bg = reset
      </Panel>
      {graph.error ? (
        <div className="ml-map__err">
          <StatusMessage tone="error">{graph.error}</StatusMessage>
        </div>
      ) : null}
    </>
  );
}
