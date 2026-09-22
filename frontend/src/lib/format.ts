/** Small text helpers shared by the dashboard, the map card and the skills list. */

/** An ISO date as a short human label, and "" for nothing — never "Invalid Date". */
export function formatDate(iso?: string | null, tz?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: iso.includes("T") ? "2-digit" : undefined,
      minute: iso.includes("T") ? "2-digit" : undefined,
      timeZone: tz || undefined,
    }).format(d);
  } catch {
    // An unknown IANA name from a brain configured on another machine must not
    // take the whole card down with it.
    return d.toISOString().slice(0, 10);
  }
}

/** The `YYYY-MM-DD` a date input wants. */
export function toDateInput(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().slice(0, 10);
}

/** Is this due date already behind us, in the brain's own timezone? */
export function isOverdue(due?: string | null, today?: string): boolean {
  if (!due) return false;
  const day = due.slice(0, 10);
  const ref = today || new Date().toISOString().slice(0, 10);
  return day < ref;
}

export function pluralize(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function truncate(s: string, max: number): string {
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

/** Title-cases an id fragment for display: `set-due` reads as "Set due". */
export function humanize(token: string): string {
  const s = token.replace(/[-_]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
}
