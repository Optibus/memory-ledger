import type { TriggerTask } from "@/api/types";

/** The order statuses are listed in: live work first, then how it ended. */
export const STATUS_ORDER = [
  "running",
  "queued",
  "failed",
  "timeout",
  "interrupted",
  "declined",
  "refused",
  "blocked",
  "done",
];

/** Tasks still moving: their card polls its detail while open. */
export const ACTIVE = new Set(["running", "queued"]);

/** Tasks the card offers to run again: what went wrong, or what the B2B
 * allowed list stopped. The server checks the same list. */
export const REPROCESSABLE = new Set(["blocked", "failed", "timeout", "interrupted"]);

export type Facet = "status" | "source" | "requester" | "name";

export function requesterOf(t: TriggerTask): string {
  return t.requester_name || t.requester_id || "someone";
}

export function facetValue(t: TriggerTask, facet: Facet): string {
  switch (facet) {
    case "status":
      return t.status;
    case "source":
      return t.source;
    case "requester":
      return requesterOf(t);
    case "name":
      return t.kind === "mention" ? "mention" : t.name;
  }
}

/** Every value a facet takes across the tasks, with how many carry it. */
export function facetCounts(tasks: TriggerTask[], facet: Facet): Map<string, number> {
  const out = new Map<string, number>();
  for (const t of tasks) {
    const v = facetValue(t, facet);
    out.set(v, (out.get(v) ?? 0) + 1);
  }
  return out;
}

/**
 * The filter keeps what is switched OFF, not what is on: a requester or a
 * command seen for the first time on the next poll shows up, instead of being
 * hidden by a selection made before it existed.
 */
export type Excluded = Partial<Record<Facet, string[]>>;

export function haystack(t: TriggerTask): string {
  return [t.name, t.kind, t.text, t.result, requesterOf(t), t.requester_id, t.source, t.scope, t.status, t.reason, t.id]
    .join("\n")
    .toLowerCase();
}

export function filterTasks(tasks: TriggerTask[], q: string, excluded: Excluded): TriggerTask[] {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const off = (Object.keys(excluded) as Facet[]).map((f) => [f, new Set(excluded[f])] as const);
  return tasks.filter((t) => {
    for (const [f, set] of off) if (set.size && set.has(facetValue(t, f))) return false;
    if (!words.length) return true;
    const hay = haystack(t);
    return words.every((w) => hay.includes(w));
  });
}

/** "3s", "4m 12s", "1h 5m": how long it took, or has been running. */
export function duration(fromIso: string, toIso?: string, now = Date.now()): string {
  if (!fromIso) return "";
  const from = Date.parse(fromIso);
  const to = toIso ? Date.parse(toIso) : now;
  if (Number.isNaN(from) || Number.isNaN(to)) return "";
  const s = Math.max(0, Math.round((to - from) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** "just now", "5m ago", "3h ago", "2d ago". */
export function ago(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (!iso || Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
