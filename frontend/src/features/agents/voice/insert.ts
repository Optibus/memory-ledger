/**
 * The dictated text of one session, kept apart from what the person typed.
 *
 * `prev` is what this session inserted last time. It is found again in the box
 * wherever the person's own typing has moved it, and swapped for `next`; if it
 * is gone (they deleted it), `next` goes to the end. So a partial keeps being
 * replaced by its final, a Cancel can take back exactly what was dictated, and
 * nothing the person typed in between is overwritten.
 */
export function applySession(value: string, prev: string, next: string): string {
  const at = prev ? value.lastIndexOf(prev) : -1;
  if (at >= 0) {
    // Taking it back also takes the space that was put before it.
    const from = !next && at > 0 && /\s/.test(value[at - 1]!) ? at - 1 : at;
    return value.slice(0, from) + next + value.slice(at + prev.length);
  }
  if (!next) return value;
  const gap = value && !/\s$/.test(value) ? " " : "";
  return value + gap + next;
}

/** The text of a session so far: its finals, then the open partial. */
export function sessionText(finals: string[], partial: string): string {
  return [...finals, partial].map((t) => t.trim()).filter(Boolean).join(" ");
}
