/**
 * Which tab owns the microphone. One voice session per user at a time, across
 * tabs: the owner refreshes a stamp in localStorage while it listens, and a
 * stamp older than a few seconds is a tab that died without letting go.
 */
const KEY = "ml.voice.owner";
const TTL_MS = 6_000;
const id = Math.random().toString(36).slice(2);

function read(): { id: string; at: number } | null {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) ?? "null") as { id: string; at: number } | null;
    return v && typeof v.at === "number" ? v : null;
  } catch {
    return null;
  }
}

/** True when this tab now holds the session. */
export function claimSession(): boolean {
  const cur = read();
  if (cur && cur.id !== id && Date.now() - cur.at < TTL_MS) return false;
  window.localStorage.setItem(KEY, JSON.stringify({ id, at: Date.now() }));
  return true;
}

export function keepSession(): void {
  const cur = read();
  if (!cur || cur.id === id) window.localStorage.setItem(KEY, JSON.stringify({ id, at: Date.now() }));
}

export function releaseSession(): void {
  if (read()?.id === id) window.localStorage.removeItem(KEY);
}
