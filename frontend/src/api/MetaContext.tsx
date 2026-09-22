import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./client";
import type { Meta } from "./types";

export interface MetaContextValue {
  meta: Meta | null;
  loading: boolean;
  /** Set when the vocabulary could not be read at all — never reported as read-only. */
  error: string | null;
  /** False under `--no-write`, and when we could not ask, so the UI hides editing. */
  writable: boolean;
  reload: () => void;
}

const MetaCtx = createContext<MetaContextValue | null>(null);

/**
 * The server's vocabulary, fetched once and shared by every section.
 *
 * It cannot change without a restart, and a restart reloads the page (see
 * `useBootReload`), so nothing re-polls it. The UI hard-coding a realm, a
 * status or a contract heading would be a second definition of something the
 * engine already owns.
 */
export function MetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    let live = true;
    setLoading(true);
    api
      .meta(ctrl.signal)
      .then((m) => {
        if (!live) return;
        setMeta(m);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!live || (err instanceof DOMException && err.name === "AbortError")) return;
        // "couldn't ask" is not "--no-write": keep them distinguishable, or a
        // dropped request silently turns the whole UI read-only with no reason.
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
      ctrl.abort();
    };
  }, [nonce]);

  return (
    <MetaCtx.Provider
      value={{
        meta,
        loading,
        error,
        writable: !!meta?.writable,
        reload: () => setNonce((n) => n + 1),
      }}
    >
      {children}
    </MetaCtx.Provider>
  );
}

export function useMeta(): MetaContextValue {
  const ctx = useContext(MetaCtx);
  if (!ctx) throw new Error("useMeta must be used inside <MetaProvider>");
  return ctx;
}

/**
 * Reloads the tab when the server restarts.
 *
 * Every process has a `boot` id. The server re-execs when its own code changes,
 * so an open tab would otherwise keep a page that predates the API behind it —
 * asking for a facet the new process renamed and rendering an empty filter that
 * looks broken rather than out of date.
 */
export function useBootReload(boot: string | undefined, onStale?: () => void): void {
  const first = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!boot) return;
    if (first.current === undefined) {
      first.current = boot;
      return;
    }
    if (first.current !== boot) {
      if (onStale) onStale();
      else window.location.reload();
    }
  }, [boot, onStale]);
}
