import { useEffect, useRef, useState } from "react";

export interface PollingOptions {
  /** How often to re-ask, in milliseconds. */
  intervalMs: number;
  /** Stop asking — the map pauses its poll while another section is open. */
  enabled?: boolean;
  /**
   * What identifies the REQUEST, not the function that makes it.
   *
   * The poll runs off a ref so a re-render never tears down a live timer, which
   * also means a changed query would otherwise not be asked until the next tick
   * — up to two minutes on the dashboard. Change this and it re-asks at once.
   */
  key?: string;
}

export interface PollingState<T> {
  data: T | null;
  error: string | null;
  /** True while the last request succeeded, so the UI can show a live dot. */
  live: boolean;
  refresh: () => void;
}

/**
 * Re-reads an endpoint on an interval, never overlapping two requests.
 *
 * Overlap was a real bug in the old page: a slow `/api/graph` on a big brain
 * left three polls in flight and the last one to land won, which made the map
 * jump backwards. One in flight at a time, and the timer restarts after the
 * response rather than on a fixed beat.
 */
export function usePolling<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  { intervalMs, enabled = true, key = "" }: PollingOptions,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ctrl = new AbortController();

    const tick = async () => {
      try {
        const next = await fnRef.current(ctrl.signal);
        if (stopped) return;
        setData(next);
        setError(null);
        setLive(true);
      } catch (err) {
        if (stopped || (err instanceof DOMException && err.name === "AbortError")) return;
        setError(err instanceof Error ? err.message : String(err));
        setLive(false);
      } finally {
        if (!stopped) timer = setTimeout(tick, intervalMs);
      }
    };

    void tick();
    return () => {
      stopped = true;
      ctrl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [intervalMs, enabled, nonce, key]);

  return { data, error, live, refresh: () => setNonce((n) => n + 1) };
}
