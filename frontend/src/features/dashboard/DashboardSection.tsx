import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/api/client";
import { useMeta } from "@/api/MetaContext";
import { usePolling } from "@/hooks/usePolling";
import { useIntervals } from "@/hooks/useIntervals";
import { useAsync } from "@/hooks/useAsync";
import { usePref } from "@/hooks/usePrefs";
import { Button, EmptyState, Spinner, StatusMessage } from "@/components";
import { Section } from "@/features/shell/Section";
import { DashboardFilters, ViewTabs } from "./DashboardFilters";
import { TaskCard } from "./TaskCard";
import { TaskComposer } from "./TaskComposer";
import { useFacets } from "./useFacets";
import { initialState, isoAdd, persistable, toQuery } from "./state";
import type { DashboardState, PersistedDashboard } from "./state";
import type { ViewPayload } from "@/api/types";

const FALLBACK_VIEWS = ["agenda", "open", "today", "tomorrow", "week", "closed", "range"];

/**
 * The date views, as a full CRUD client.
 *
 * Everything here is plain SQL through the engine's `api_*` helpers — no model
 * call and no tokens. Leaving this section open costs nothing.
 */
export function DashboardSection() {
  const { writable, error: metaError } = useMeta();
  // Which view, which filters — remembered between visits (see `persistable`).
  const [saved, setSaved, prefsReady] = usePref<Partial<PersistedDashboard>>("dashboard", {});
  const [state, setState] = useState<DashboardState>(() => ({ ...initialState(), ...saved }));
  const [composing, setComposing] = useState(false);
  const hydrated = useRef(false);

  // The provider may still be fetching the brain's copy when this mounts, so
  // the stored state is applied when it lands — once, so it can never clobber a
  // filter the person changed while it was in flight.
  useEffect(() => {
    if (!prefsReady || hydrated.current) return;
    hydrated.current = true;
    setState((s) => ({ ...s, ...saved }));
  }, [prefsReady, saved]);

  // And written back on every change. Guarded on `hydrated` so the defaults this
  // component starts with never overwrite what was stored before they arrive.
  useEffect(() => {
    if (hydrated.current) setSaved(persistable(state));
  }, [state, setSaved]);
  const facets = useFacets();
  const { dashRefreshMs } = useIntervals();

  const views = useAsync(async (signal) => (await api.views(signal)).views, []);
  const viewNames = views.data?.length ? views.data : FALLBACK_VIEWS;

  const query = useMemo(() => toQuery(state), [state]);
  const queryKey = useMemo(() => JSON.stringify(query), [query]);

  const fetchView = useCallback(
    (signal: AbortSignal) => api.view(state.view, query, signal),
    // queryKey is the value that actually changes; `query` is a fresh object
    // each render and would restart the poll on every keystroke elsewhere.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.view, queryKey],
  );

  const poll = usePolling<ViewPayload>(fetchView, {
    intervalMs: dashRefreshMs,
    key: `${state.view}${queryKey}`,
  });
  const today = isoAdd(0);

  const onChanged = () => poll.refresh();
  const onFacetsChanged = () => {
    void facets.refresh();
    poll.refresh();
  };

  return (
    <Section
      title="Dashboard"
      lead="The date views, straight from the query layer the CLI uses. No model, no tokens — leaving this open costs nothing."
    >
      <ViewTabs
        views={viewNames}
        value={state.view}
        onChange={(view) => setState((s) => ({ ...s, view }))}
      />

      <DashboardFilters state={state} onChange={setState} facets={facets} />

      {!writable ? (
        <StatusMessage>
          {metaError
            ? "Writes unavailable — the server vocabulary could not be read."
            : "read-only server (--no-write) — editing disabled"}
        </StatusMessage>
      ) : composing ? (
        <TaskComposer
          facets={facets}
          preferredRealm={state.realms.length === 1 ? state.realms[0]! : facets.defaultRealm}
          onCancel={() => setComposing(false)}
          onCreated={() => {
            setComposing(false);
            onFacetsChanged();
          }}
        />
      ) : (
        <Button variant="primary" onClick={() => setComposing(true)}>＋ New task</Button>
      )}

      <p className="ml-dash__meta">
        {poll.error ? (
          <StatusMessage tone="error">{poll.error}</StatusMessage>
        ) : poll.data ? (
          `${poll.data.title} — ${poll.data.count} item${poll.data.count === 1 ? "" : "s"}`
        ) : (
          <Spinner label="loading the view" />
        )}
      </p>

      {poll.data && poll.data.results.length === 0 ? (
        <EmptyState title="Nothing here. Clear head." hint="No row in this view matches the filters above." />
      ) : null}

      {poll.data?.results.map((row) => (
        <TaskCard
          key={row.id}
          row={row}
          today={today}
          onChanged={onChanged}
          onFacetsChanged={onFacetsChanged}
        />
      ))}
    </Section>
  );
}
