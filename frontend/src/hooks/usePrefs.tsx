import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/api/client";
import type { UiPrefs } from "@/api/types";

/**
 * The view state the UI used to forget on every reload.
 *
 * Which dashboard view you had open, how far back "closed" looks, what the map
 * has filtered out — none of it changes what the engine computes, so none of it
 * belongs in `settings.json` beside the timezone (CONVENTIONS §2g). It belongs
 * to the browser, and it lives in `localStorage`.
 *
 * Syncing it to the brain is then opt-in, one setting (`ui_sync`), for the case
 * localStorage cannot serve: the SAME person on a SECOND machine. With it on,
 * the brain's copy is what a cold browser starts from instead of the defaults —
 * and if the brain has no copy yet, the defaults still apply, so turning it on
 * never blanks a view.
 *
 * Local always wins the race and the network is never in the way: every change
 * is written to localStorage synchronously and pushed afterwards, debounced. A
 * brain that is unreachable costs you cross-machine sync, never your own UI.
 */

const STORAGE_KEY = "ml:prefs";
/** Long enough that dragging a slider is one write, short enough to survive a
 *  tab closed right after a click. */
const PUSH_DEBOUNCE_MS = 800;

interface PrefsContextValue {
  prefs: UiPrefs;
  setPref: (key: string, value: unknown) => void;
  /** True once the initial read (local, then the brain if syncing) has settled.
   *  Views wait for it, so a synced pref never loses a race to its default. */
  ready: boolean;
  syncing: boolean;
}

const PrefsContext = createContext<PrefsContextValue | null>(null);

/** Reads that must never throw: a private window, blocked site data, or a
 *  hand-corrupted value all have to degrade to "no preferences yet". */
function readLocal(): UiPrefs {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const val: unknown = JSON.parse(raw);
    return val && typeof val === "object" && !Array.isArray(val) ? (val as UiPrefs) : {};
  } catch {
    return {};
  }
}

/** Value equality for preference values — scalars, arrays and plain objects. */
function same(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

function writeLocal(prefs: UiPrefs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage full, or disabled. The session keeps working in memory.
  }
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<UiPrefs>(() => readLocal());
  const [ready, setReady] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const timer = useRef<number | null>(null);
  // Tracks whether a change came from the user or from hydrating the server's
  // copy. Pushing what we just pulled is a write loop with extra steps.
  const dirty = useRef(false);

  // Hydrate from the brain once, on mount. The server answers with `synced`, so
  // one request settles both "should I sync?" and "here is what you had".
  useEffect(() => {
    const ctrl = new AbortController();
    api
      .uiPrefs(ctrl.signal)
      .then((res) => {
        setSyncing(res.synced);
        // Server wins on hydrate: it is the cross-machine copy, and this only
        // runs when the person asked for that. Local keys it does not know
        // survive, so a preference set here before syncing was on is not lost.
        if (res.synced && res.prefs && Object.keys(res.prefs).length) {
          setPrefs((local) => {
            const merged = { ...local, ...res.prefs };
            writeLocal(merged);
            return merged;
          });
        }
      })
      .catch(() => {
        // Offline, or a read-only server. localStorage already answered.
      })
      .finally(() => setReady(true));
    return () => ctrl.abort();
  }, []);

  // Push, debounced, and only what the user actually changed.
  useEffect(() => {
    if (!syncing || !dirty.current) return;
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      dirty.current = false;
      void api.saveUiPrefs(prefs).catch(() => {
        // A failed push is not worth interrupting anyone over: the value is
        // already stored locally, and the next change retries.
      });
    }, PUSH_DEBOUNCE_MS);
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [prefs, syncing]);

  const setPref = useCallback((key: string, value: unknown) => {
    setPrefs((cur) => {
      // Compared by content, not identity. Views persist a derived object, so
      // a fresh object holding the same values arrives on every render — by
      // identity that is a change, and each one would cost a localStorage write
      // and a POST to say nothing.
      if (same(cur[key], value)) return cur;
      dirty.current = true;
      const next = { ...cur, [key]: value };
      writeLocal(next);              // synchronous: a reload right now keeps it
      return next;
    });
  }, []);

  // The Settings page flips `ui_sync` server-side; this keeps the provider in
  // step without a reload, and pushes the current state so the brain's first
  // copy is what you are looking at rather than an empty object.
  useEffect(() => {
    const onSync = (e: Event) => {
      const on = Boolean((e as CustomEvent<boolean>).detail);
      setSyncing(on);
      if (on) dirty.current = true;
    };
    window.addEventListener("ml:ui-sync", onSync);
    return () => window.removeEventListener("ml:ui-sync", onSync);
  }, []);

  const value = useMemo(
    () => ({ prefs, setPref, ready, syncing }),
    [prefs, setPref, ready, syncing],
  );
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

/**
 * One preference, with the same shape as useState.
 *
 * `ready` is exposed so a view can hold its first render until the stored value
 * has arrived — without it, a synced preference is overwritten by its own
 * default the moment the component mounts.
 */
export function usePref<T>(key: string, fallback: T): [T, (v: T) => void, boolean] {
  const ctx = useContext(PrefsContext);
  // Loud on purpose. A silent in-memory fallback would make a component that
  // escaped the provider look like it works, and only reveal itself as "this
  // one view forgets everything" much later.
  if (!ctx) throw new Error("usePref must be used inside <PrefsProvider>");
  const raw = ctx.prefs[key];
  const value = (raw === undefined ? fallback : raw) as T;
  const set = useCallback((v: T) => ctx.setPref(key, v), [ctx, key]);
  return [value, set, ctx.ready];
}

/** Whether prefs are being kept in the brain, for the Settings page to show. */
export function usePrefsSync(): { syncing: boolean; ready: boolean } {
  const ctx = useContext(PrefsContext);
  return { syncing: Boolean(ctx?.syncing), ready: Boolean(ctx?.ready) };
}
