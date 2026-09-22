/**
 * Node types and realms are OPEN vocabulary — anyone can invent one — so the
 * palette below is a set of nice defaults, not the list.
 */
export const TYPE_COLORS: Record<string, string> = {
  person: "#58a6ff",
  task: "#3fb950",
  subtask: "#56d364",
  requirement: "#d29922",
  decision: "#db61a2",
  note: "#8b949e",
  project: "#bc8cff",
  reminder: "#f0883e",
  "writing-sample": "#79c0ff",
  association: "#a5d6ff",
  meeting: "#e3b341",
  "jira-issue": "#f778ba",
  resource: "#39c5cf",
  skill: "#ff7b72",
};

export const UNKNOWN_COLOR = "#8b949e";

/** A stable hue from the text itself: same word, same colour, every session. */
export function hueOf(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  const mixed = Math.imul(h, 2654435761) >>> 0;
  return mixed ? mixed % 360 : 0;
}

export function colorForType(type: string): string {
  if (TYPE_COLORS[type]) return TYPE_COLORS[type];
  return type ? `hsl(${hueOf(type)} 55% 62%)` : UNKNOWN_COLOR;
}

/**
 * Realms are few and always rendered as a set, so spacing them evenly around
 * the wheel beats hashing — hashing put two realms 12 degrees apart, which
 * reads as the same colour on an 11px swatch.
 */
export function realmPalette(keys: string[]): (key: string) => string {
  const step = 360 / Math.max(keys.length, 1);
  const map: Record<string, string> = {};
  keys.forEach((k, i) => {
    map[k] = `hsl(${Math.round(i * step + 20)} 60% 60%)`;
  });
  return (k: string) => map[k] ?? UNKNOWN_COLOR;
}
