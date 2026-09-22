import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * One fetch, with loading and error, aborted when the component goes away.
 *
 * The old page had thirty bare `await fetch(...)` calls, each with its own idea
 * of what to do when the server said no; several did nothing at all.
 */
export function useAsync<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
): AsyncState<T> & { reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    fn(ctrl.signal)
      .then((data) => {
        if (live) setState({ data, error: null, loading: false });
      })
      .catch((err: unknown) => {
        // An abort is the component leaving, not a failure worth reporting.
        if (!live || (err instanceof DOMException && err.name === "AbortError")) return;
        setState({ data: null, error: err instanceof Error ? err.message : String(err), loading: false });
      });
    return () => {
      live = false;
      ctrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload };
}
