import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import type { RealmCount } from "@/api/types";

export interface Facets {
  assignees: Record<string, number>;
  realms: Record<string, RealmCount>;
  kinds: Record<string, RealmCount>;
  defaultRealm: string;
  /** Re-read after a write, so a task created in a brand-new realm is filterable at once. */
  refresh: () => Promise<void>;
}

/**
 * The dashboard's filter vocabulary, read from the brain rather than shipped.
 *
 * All three are open vocabulary — invent a realm with `--realm skunkworks` and
 * it is a chip on the next refresh, with no code change here.
 */
export function useFacets(): Facets {
  const [assignees, setAssignees] = useState<Record<string, number>>({});
  const [realms, setRealms] = useState<Record<string, RealmCount>>({});
  const [kinds, setKinds] = useState<Record<string, RealmCount>>({});
  const [defaultRealm, setDefaultRealm] = useState("");

  const refresh = useCallback(async () => {
    // Settled, not all: one facet endpoint failing must not blank the other two.
    const [a, r, k] = await Promise.allSettled([api.assignees(), api.realms(), api.kinds()]);
    if (a.status === "fulfilled") setAssignees(a.value.assignees || {});
    if (r.status === "fulfilled") {
      setRealms(r.value.realms || {});
      setDefaultRealm(r.value.default || "");
    }
    if (k.status === "fulfilled") setKinds(k.value.kinds || {});
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { assignees, realms, kinds, defaultRealm, refresh };
}
