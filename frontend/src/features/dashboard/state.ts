/** Everything the dashboard's URL would carry, in one shape. */
export interface DashboardState {
  view: string;
  from: string;
  to: string;
  status: string;
  sinceDays: number;
  mine: boolean;
  assignees: string[];
  realms: string[];
  kinds: string[];
}

export function isoAdd(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function initialState(view = "agenda"): DashboardState {
  return {
    view,
    from: isoAdd(0),
    to: isoAdd(14),
    status: "any",
    sinceDays: 7,
    mine: false,
    assignees: [],
    realms: [],
    kinds: [],
  };
}

/**
 * The query the server sees.
 *
 * Only what the chosen view actually reads is sent, so a `since_days` left over
 * from the closed view never rides along into `agenda`.
 */
export function toQuery(s: DashboardState): Record<string, string> {
  const q: Record<string, string> = {};
  if (s.view === "range") {
    q.from = s.from;
    q.to = s.to;
    q.status = s.status;
  }
  if (s.view === "closed") q.since_days = String(s.sinceDays);
  if (s.mine) q.mine = "1";
  if (s.assignees.length) q.assignee = s.assignees.join(",");
  if (s.realms.length) q.realm = s.realms.join(",");
  if (s.kinds.length) q.kind = s.kinds.join(",");
  return q;
}

/**
 * The part of the dashboard's state worth remembering between visits.
 *
 * `from` and `to` are deliberately absent. They are a rolling window —
 * `initialState` sets them to today and today+14 — so storing the literal dates
 * would bring back a fortnight that ended weeks ago and call it your default.
 * Everything else describes what you are looking FOR, not when, and that does
 * survive a reload.
 */
export type PersistedDashboard = Pick<
  DashboardState,
  "view" | "status" | "sinceDays" | "mine" | "assignees" | "realms" | "kinds"
>;

export function persistable(s: DashboardState): PersistedDashboard {
  return {
    view: s.view,
    status: s.status,
    sinceDays: s.sinceDays,
    mine: s.mine,
    assignees: s.assignees,
    realms: s.realms,
    kinds: s.kinds,
  };
}
