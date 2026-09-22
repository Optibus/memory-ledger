/** Statuses that mean the work is behind you. */
export const DONE_STATUSES = new Set(["done", "cancelled"]);

/**
 * Types that have a meaningful "done".
 *
 * Deliberately an allowlist rather than "has a non-empty status": a `subtask`
 * is created with an empty status and would be wrongly excluded, a `skill` node
 * carries status "active" and completing it would break skill matching, and a
 * `jira-issue` mirrors state owned elsewhere.
 */
export const COMPLETABLE_TYPES = new Set(["task", "subtask", "reminder"]);

export function isDone(status?: string): boolean {
  return !!status && DONE_STATUSES.has(status);
}

export function isCompletable(type: string): boolean {
  return COMPLETABLE_TYPES.has(type);
}
