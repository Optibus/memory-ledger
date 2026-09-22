import { api } from "@/api/client";
import { useAsync } from "./useAsync";
import type { Settings } from "@/api/types";

export interface Intervals {
  mapPollMs: number;
  dashRefreshMs: number;
}

const DEFAULTS: Intervals = { mapPollMs: 2000, dashRefreshMs: 120_000 };

/**
 * How often the map and the dashboard re-read, from the brain's own settings.
 *
 * The cadence is a preference in `<home>/settings.json`, not a constant: a
 * shared brain on a slow link wants a slower map, and that answer has to hold
 * for every client reading the same file.
 */
export function useIntervals(): Intervals {
  const { data } = useAsync<Settings>((signal) => api.settings(signal), []);
  const eff = data?.effective;
  return {
    mapPollMs: Math.max(250, (eff?.map_poll_seconds ?? 2) * 1000) || DEFAULTS.mapPollMs,
    dashRefreshMs: Math.max(5000, (eff?.dash_refresh_seconds ?? 120) * 1000) || DEFAULTS.dashRefreshMs,
  };
}
