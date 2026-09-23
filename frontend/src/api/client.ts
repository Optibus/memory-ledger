/**
 * One way to reach the server.
 *
 * Every call in the app goes through `get` or `post` here, so retry, error
 * shaping, the write-disabled (`--no-write`) 403 and the abort-on-unmount path
 * are decided once instead of at each of the fifty call sites the old page had.
 */
import type {
  Envelope,
  ExportBundleResult,
  GraphPayload,
  ImportBundleResult,
  ImportChoice,
  LinkSuggestion,
  Meta,
  NodeDetail,
  Person,
  RealmCount,
  SearchHit,
  Settings,
  SkillsPayload,
  Status,
  SyncResult,
  Timezones,
  UploadBundleResult,
  ViewPayload,
} from "./types";

/** A failed call, carrying the server's own reason rather than a status code. */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The server is same-origin in production; the dev server proxies /api to it. */
export const BASE = "";

type Query = Record<string, string | number | boolean | undefined | null>;

export function queryString(params: Query = {}): string {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    usp.set(key, String(value));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

async function unwrap<T>(res: Response): Promise<T> {
  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    throw new ApiError(
      `${res.status} ${res.statusText || "unreadable response"}`,
      res.status,
    );
  }
  const body = payload as Partial<Envelope>;
  // `ok: false` is the server's own refusal and carries the reason worth
  // showing. An HTTP failure with no body of its own falls back to the status.
  if (!res.ok || body?.ok === false) {
    throw new ApiError(body?.error || `request failed (${res.status})`, res.status);
  }
  return payload as T;
}

export async function get<T>(path: string, params?: Query, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}${queryString(params)}`, {
    signal,
    headers: { accept: "application/json" },
  });
  return unwrap<T>(res);
}

export async function post<T = Envelope>(
  path: string,
  body: unknown = {},
  signal?: AbortSignal,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    signal,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap<T>(res);
}

/**
 * The named surface. Everything the UI can ask the brain, in one place, so a
 * component never builds a URL and a renamed endpoint is one edit.
 */
export const api = {
  // reads
  graph: (signal?: AbortSignal) => get<GraphPayload>("/api/graph", undefined, signal),
  meta: (signal?: AbortSignal) => get<Meta>("/api/meta", undefined, signal),
  views: (signal?: AbortSignal) =>
    get<{ ok: boolean; views: string[] }>("/api/views", undefined, signal),
  view: (name: string, params: Query, signal?: AbortSignal) =>
    get<ViewPayload>(`/api/${name}`, params, signal),
  node: (id: string, signal?: AbortSignal) => get<NodeDetail>("/api/node", { id }, signal),
  people: (signal?: AbortSignal) =>
    get<{ ok: boolean; people: Person[] }>("/api/people", undefined, signal),
  personDuplicates: (signal?: AbortSignal) =>
    get<import("./types").PersonDuplicates>("/api/person-duplicates", undefined, signal),
  assignees: (signal?: AbortSignal) =>
    get<{ ok: boolean; assignees: Record<string, number> }>("/api/assignees", undefined, signal),
  realms: (signal?: AbortSignal) =>
    get<{ ok: boolean; realms: Record<string, RealmCount>; default: string }>(
      "/api/realms",
      undefined,
      signal,
    ),
  kinds: (signal?: AbortSignal) =>
    get<{ ok: boolean; kinds: Record<string, RealmCount>; none_token: string }>(
      "/api/kinds",
      undefined,
      signal,
    ),
  skills: (signal?: AbortSignal) => get<SkillsPayload>("/api/skills", undefined, signal),
  rule: (id: string, signal?: AbortSignal) =>
    get<{ ok: boolean; rule: import("./types").RuleNode | null }>("/api/rule", { id }, signal),
  search: (q: string, signal?: AbortSignal) =>
    get<{ ok: boolean; results: SearchHit[] }>("/api/search", { q }, signal),
  suggestLinks: (text: string, exclude?: string, signal?: AbortSignal) =>
    get<{ ok: boolean; count: number; suggestions: LinkSuggestion[] }>(
      "/api/suggest-links",
      { text, exclude },
      signal,
    ),
  settings: (signal?: AbortSignal) => get<Settings>("/api/settings", undefined, signal),
  timezones: (signal?: AbortSignal) => get<Timezones>("/api/timezones", undefined, signal),
  status: (signal?: AbortSignal) => get<Status>("/api/status", undefined, signal),
  trigger: (signal?: AbortSignal) =>
    get<import("./types").TriggerStatus>("/api/trigger", { limit: 5 }, signal),
  capture: (signal?: AbortSignal) =>
    get<import("./types").CaptureState>("/api/capture", undefined, signal),
  doctor: (signal?: AbortSignal) =>
    get<import("./types").DoctorReport>("/api/doctor", undefined, signal),
  backups: (signal?: AbortSignal) =>
    get<import("./types").BackupList>("/api/backups", undefined, signal),
  uiPrefs: (signal?: AbortSignal) =>
    get<import("./types").UiPrefsPayload>("/api/ui-prefs", undefined, signal),
  /** What consolidation WOULD do. A read: it changes nothing. */
  consolidatePreview: (keepDays: number, signal?: AbortSignal) =>
    get<import("./types").ConsolidationPreview>(
      "/api/consolidate-preview",
      { keep_days: String(keepDays) },
      signal,
    ),
  bundleUrl: (name: string) => `${BASE}/api/bundle${queryString({ name })}`,

  // writes
  addTask: (body: Record<string, unknown>) => post("/api/add-task", body),
  setDue: (id: string, due: string) => post("/api/set-due", { id, due }),
  complete: (id: string, note?: string) => post("/api/complete", { id, note }),
  setStatus: (id: string, status: string, waiting_on?: string) =>
    post("/api/set-status", { id, status, waiting_on }),
  setAssignee: (id: string, assignee: string) => post("/api/set-assignee", { id, assignee }),
  updateNode: (body: Record<string, unknown>) => post("/api/update-node", body),
  link: (src: string, dst: string, rel: string) => post("/api/link", { src, dst, rel }),
  unlink: (src: string, dst: string, rel: string) => post("/api/unlink", { src, dst, rel }),
  assign: (body: Record<string, unknown>) => post("/api/assign", body),
  /** Named, not a loose record: the server reads `canonical` and `duplicate`,
   *  and an object literal is exactly the kind of thing that drifts silently. */
  mergePerson: (canonical: string, duplicate: string) =>
    post("/api/merge-person", { canonical, duplicate }),
  addAlias: (id: string, alias: string) => post("/api/add-alias", { id, alias }),
  saveSettings: (body: Partial<import("./types").StoredSettings>) => post("/api/settings", body),
  saveNeo4j: (body: Record<string, unknown>) => post("/api/neo4j-config", body),
  testNeo4j: () => post("/api/neo4j-test", {}),
  /** A patch. Secrets are write-only: send one to replace it, list it in
   *  `clear` to remove it, leave it out to keep what is stored. */
  saveTrigger: (body: Record<string, unknown>) =>
    post<{ ok: boolean; config: import("./types").TriggerConfig; runtime: import("./types").TriggerRuntime }>(
      "/api/trigger-config",
      body,
    ),
  /** Open (`enabled: true`) or close a channel. `channel` may be a link. */
  setTriggerChannel: (channel: string, enabled: boolean, name = "") =>
    post<{ ok: boolean; channels: import("./types").TriggerChannels }>("/api/trigger-channel", {
      channel,
      name,
      enabled,
    }),
  checkTrigger: (only: import("./types").TriggerService) =>
    post<import("./types").TriggerCheck>("/api/trigger-check", { only }),
  flush: () => post("/api/flush", {}),
  /**
   * Consolidate. Refused without `confirm`, by the server — the preview is not
   * decoration, it is the step before this one.
   */
  prune: (keep_days: number, confirm: boolean) =>
    post<import("./types").MaintainResult>("/api/prune", { keep_days, confirm }),
  setCapture: (enabled: boolean) =>
    post<{ ok: boolean; capture: import("./types").CaptureState }>("/api/capture", { enabled }),
  saveUiPrefs: (prefs: import("./types").UiPrefs) =>
    post<{ ok: boolean; synced: boolean }>("/api/ui-prefs", { prefs }),
  backup: (label?: string) =>
    post<import("./types").BackupList & { created: import("./types").Backup }>(
      "/api/backup",
      { label },
    ),
  createRule: (body: Record<string, unknown>) => post("/api/create-rule", body),
  createSkill: (body: Record<string, unknown>) => post("/api/create-skill", body),
  exportSkills: (body: Record<string, unknown> = {}) => post<SyncResult>("/api/export-skills", body),
  ingestSkills: (body: Record<string, unknown> = {}) => post<SyncResult>("/api/ingest-skills", body),
  createProcess: (body: { title: string; body?: string; trigger?: string; realm?: string; id?: string }) =>
    post<{ ok: boolean; node: { id: string; realm: string }; action: string }>("/api/create-process", body),
  exportBundle: (skills: string[], rules: string[], processes: string[] = []) =>
    post<ExportBundleResult>("/api/export-bundle", { skills, rules, processes }),
  uploadBundle: (filename: string, data: string) =>
    post<UploadBundleResult>("/api/upload-bundle", { filename, data }),
  importBundle: (token: string, select: Record<string, ImportChoice>, processRealm?: string) =>
    post<ImportBundleResult>(
      "/api/import-bundle",
      processRealm ? { token, select, process_realm: processRealm } : { token, select },
    ),
  deleteContracts: (ids: string[], force = false) =>
    post<import("./types").DeleteContractsResult>("/api/delete-contracts", { ids, force }),
};

export type Api = typeof api;
