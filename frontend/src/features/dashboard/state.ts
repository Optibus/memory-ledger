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
