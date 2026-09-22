import { useCallback, useMemo, useState } from "react";

/**
 * Which corpus an id belongs to. Skills are always `skill_*`, but a process is
 * any node of type `process` — many were retyped from decisions and notes and
 * kept their ids — so the caller passes the process ids it actually loaded.
 */
export function kindOf(
  id: string,
  processIds?: ReadonlySet<string>,
): "skills" | "rules" | "processes" {
  if (id.startsWith("skill_")) return "skills";
  if (processIds?.has(id) || id.startsWith("process_")) return "processes";
  return "rules";
}

export interface Selection {
  picking: boolean;
  picked: Set<string>;
  setPicking: (on: boolean) => void;
  toggle: (id: string, want?: boolean) => void;
  selectAll: (ids: string[]) => void;
  clear: () => void;
  /** The two lists the bundle endpoints want. */
  split: (processIds?: ReadonlySet<string>) => {
    skills: string[];
    rules: string[];
    processes: string[];
  };
}

/** Selection mode: checkboxes on every card, then export or delete what you ticked. */
export function useSelection(): Selection {
  const [picking, setPickingState] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const setPicking = useCallback((on: boolean) => {
    setPickingState(on);
    // Leaving selection mode drops the selection: a hidden tick that survives
    // into the next session is the kind of thing that deletes the wrong rule.
    if (!on) setPicked(new Set());
  }, []);

  const toggle = useCallback((id: string, want?: boolean) => {
    setPicked((prev) => {
      const next = new Set(prev);
      const on = want ?? !next.has(id);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const selectAll = useCallback((ids: string[]) => setPicked(new Set(ids)), []);
  const clear = useCallback(() => setPicked(new Set()), []);

  const split = useCallback((processIds?: ReadonlySet<string>) => {
    const out = { skills: [] as string[], rules: [] as string[], processes: [] as string[] };
    for (const id of picked) out[kindOf(id, processIds)].push(id);
    return out;
  }, [picked]);

  return useMemo(
    () => ({ picking, picked, setPicking, toggle, selectAll, clear, split }),
    [picking, picked, setPicking, toggle, selectAll, clear, split],
  );
}
