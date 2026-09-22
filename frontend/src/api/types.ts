/**
 * The wire shapes of the memory-ledger HTTP API.
 *
 * These mirror `docs/openapi.yaml`, which is the contract of record, and
 * `scripts/openapi.py` builds that file from the same route table the server
 * dispatches on — so a route the server grows without a spec entry fails CI
 * (`tests/test_openapi.py`) rather than quietly drifting away from these types.
 */

/** Every response carries `ok`; a failure carries `error` with it. */
export interface Envelope {
  ok: boolean;
  error?: string;
}

// ---------------------------------------------------------------------------
// Graph / map
// ---------------------------------------------------------------------------

export interface GraphNode {
  id: string;
  title: string;
  type: string;
  realm: string;
  status?: string;
  due?: string;
  assignee?: string;
  body?: string;
  kind?: string;
  degree?: number;
  props?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface GraphEdge {
  src: string;
  dst: string;
  rel: string;
}

export interface GraphMeta {
  count?: number;
  edges?: number;
  by_type?: Record<string, number>;
  by_realm?: Record<string, number>;
  by_kind?: Record<string, number>;
  boot?: string;
  backend?: string;
  /** The person node the owner setting resolves to — the map's centre. */
  ego?: string;
  error?: string;
}

export interface GraphPayload extends Envelope {
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta: GraphMeta;
}

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

export interface SectionShape {
  field: string;
  heading: string;
  required: boolean;
  generated?: boolean;
}

export interface DeclLabel {
  field: string;
  label: string;
}

export interface Meta extends Envelope {
  rels: string[];
  statuses: string[];
  /** realm -> two counts. A map, because it is read by name everywhere. */
  realms: Record<string, RealmCount>;
  default_realm: string;
  kinds: Record<string, RealmCount>;
  no_kind_token: string;
  /** type -> how many of it the brain holds. */
  types: Record<string, number>;
  skill_sections: SectionShape[];
  rule_sections: SectionShape[];
  decl_labels: DeclLabel[];
  rule_sources: string[];
  node_types: string[];
  writable: boolean;
  boot: string;
}

/**
 * Two counts per realm because they answer different questions: `nodes` is what
 * the map filters over, `open_tasks` is what the dashboard filters over. A realm
 * with 40 nodes and 0 open tasks is real, and the chip says so rather than
 * showing a number that filters down to nothing.
 */
export interface RealmCount {
  nodes: number;
  open_tasks: number;
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

/** The slim row `brain_query._slim` sends — small on purpose. */
export interface TaskRow {
  id: string;
  title: string;
  type: string;
  status?: string;
  realm?: string;
  due?: string;
  /** The due date already resolved into the brain's timezone, or "--". */
  due_local?: string;
  waiting_on?: string;
  assignee?: string;
  kind?: string;
  delegated?: string;
  mine?: boolean;
  blocked_by?: RelatedRef[];
  blocks?: RelatedRef[];
  related?: RelatedRef[];
  [key: string]: unknown;
}

export interface RelatedRef {
  id: string;
  title: string;
  type?: string;
  status?: string;
}

export interface ViewPayload extends Envelope {
  view: string;
  /** The view's own heading — "Agenda", "Due this week", … */
  title: string;
  /** The date the view was computed against. */
  ref?: string;
  count: number;
  results: TaskRow[];
}

/** `/api/node` returns the node's fields inline, plus its edges either way. */
export interface NodeDetail extends Envelope {
  id: string;
  type: string;
  title: string;
  body?: string;
  status?: string;
  realm?: string;
  due?: string;
  props?: Record<string, unknown>;
  /** Named neighbours, each carrying its own direction. */
  edges: NodeEdge[];
  [key: string]: unknown;
}

export interface NodeEdge {
  dir: "out" | "in";
  rel: string;
  id: string;
  title: string;
  type?: string;
}

export interface Person {
  id: string;
  title: string;
  aliases: string[];
  realm: string;
}

export interface LinkSuggestion {
  id: string;
  title: string;
  type: string;
  rel: string;
  /** The wording in the text that produced this guess. */
  cue?: string;
}

export interface SearchHit {
  id: string;
  title: string;
  type: string;
  realm?: string;
  status?: string;
}

// ---------------------------------------------------------------------------
// Skills and rules
// ---------------------------------------------------------------------------

/** Why a rule is on a skill's card — the three are genuinely different. */
export type RuleWhy = "declared" | "matched" | "always";

export interface SkillRuleRef {
  id: string;
  title: string;
  source?: string;
  non_negotiable?: boolean;
  linked?: boolean;
  why: RuleWhy;
}

export interface SkillNode {
  id: string;
  title: string;
  description: string;
  triggers: string;
  kind: string;
  uses: number;
  requires: string[];
  waits_for: string[];
  enriched_by: string[];
  reads: string[];
  writes: string[];
  reads_other: string;
  writes_other: string;
  domains: string[];
  declared: boolean;
  rules: SkillRuleRef[];
  /** Nesting is the `requires` edge and nothing else (CONVENTIONS §5a). */
  children: SkillNode[];
}

export interface SkillTree {
  kind: string;
  hub: string;
  title: string;
  count: number;
  skills: SkillNode[];
}

export interface RuleNode {
  id: string;
  title: string;
  source: string;
  section: string;
  non_negotiable: boolean;
  /** What match-rules actually fires on — filtering by trigger has to see it. */
  scenario: string;
  domains: string[];
  chars?: number;
  body?: string;
  rule?: string;
  sections?: Record<string, string>;
  [key: string]: unknown;
}

/** A way of working: a `process` node, part-of `hub_process`, scoped to a realm. */
export interface ProcessNode {
  id: string;
  title: string;
  body: string;
  realm: string;
  /** When this process applies. */
  trigger: string;
  established: string;
  category: string;
  domains: string[];
  edges: number;
  updated: string;
}

export interface NodeTypeInfo {
  type: string;
  description?: string;
  count?: number;
  readers?: string[];
  writers?: string[];
}

export interface SkillsPayload extends Envelope {
  trees: SkillTree[];
  rules: RuleNode[];
  /** Every realm's processes; the page narrows, defaulting to `realm`. */
  processes?: ProcessNode[];
  /** The realm a new or imported process lands in. */
  realm?: string;
  node_types: NodeTypeInfo[];
  /** hub id -> the human title a filter labels itself with. */
  areas: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Bundles
// ---------------------------------------------------------------------------

export interface ExportBundleResult extends Envelope {
  skills: number;
  rules: number;
  processes?: number;
  /** Where the zip landed on disk — the "two machines" case wants this. */
  path: string;
  name: string;
  /** A same-origin URL, so the browser downloads it too. */
  download: string;
  /** Required by something exported, but not carried — those links will not form. */
  missing_skills?: string[];
  cycles?: string[][];
}

export interface BundleItem {
  id: string;
  title: string;
  description?: string;
  kind: "skill" | "rule" | "process";
  /** For a process: the realm it was exported from. */
  realm?: string;
  /** The only interesting case: this brain already holds it. */
  exists: boolean;
}

export interface UploadBundleResult extends Envelope {
  token: string;
  counts: { skills?: number; rules?: number; processes?: number };
  items: BundleItem[];
}

export type ImportChoice = "update" | "replace" | "ignore";

export interface ImportBundleResult extends Envelope {
  applied: { id: string; action: string; rules_not_found?: string[] }[];
  tree?: { added: number; pruned: number };
  missing_skills?: string[];
}

export interface SyncResult extends Envelope {
  /** export-skills */
  dir?: string;
  skills?: number;
  rules?: { id: string; action: string }[] | number;
  /** ingest-skills */
  count?: number;
  ingested?: { id: string; action: string; rules_not_found?: string[] }[];
  tree?: { added: number; pruned: number };
}

export interface DeleteContractsResult extends Envelope {
  count: number;
  deleted: { id: string; type: string }[];
  refused: { id: string; why: string }[];
  not_found: string[];
}

// ---------------------------------------------------------------------------
// Settings and status
// ---------------------------------------------------------------------------

export interface Settings extends Envelope {
  writable: boolean;
  keys: string[];
  /** What the file holds. Empty for a key nobody ever set. */
  settings: Partial<StoredSettings>;
  /**
   * Every key a shell variable has taken over. A settings page that hides this
   * is lying about what a save will do.
   */
  env: Record<string, string>;
  path: string;
  effective: EffectiveSettings;
  capture: CaptureState;
}

export interface StoredSettings {
  tz: string;
  realm: string;
  /** Who "me" is — what `--mine` filters on. */
  owner: string;
  backend: string;
  /** Push each write to the neo4j mirror, instead of only queueing it. */
  autosync: boolean;
  /** Let the daily hook fold closed days into digests. */
  consolidate: boolean;
  /** Keep this browser's view preferences in the brain, not just locally. */
  ui_sync: boolean;
  map_poll_seconds: number;
  dash_refresh_seconds: number;
}

/** Where a resolved value came from. The page says so, because a setting whose
 *  env var has taken over is a field that cannot do what it looks like it does. */
export type SettingSource = "env" | "settings" | "default";

export interface EffectiveSettings {
  tz: string;
  tz_source: SettingSource | "system";
  system_tz: string;
  now: string;
  realm: string;
  owner: string;
  owner_source: SettingSource;
  backend: string;
  backends: string[];
  autosync: boolean;
  autosync_source: SettingSource;
  consolidate: boolean;
  consolidate_source: SettingSource;
  ui_sync: boolean;
  ui_sync_source: SettingSource;
  map_poll_seconds: number;
  dash_refresh_seconds: number;
}

/**
 * Whether the ledger is recording, and which switch decided it.
 *
 * `env_off` matters: $MEMORY_LEDGER_OFF outranks the marker file, so when it is
 * set the UI must not offer to turn capture back on — it cannot.
 */
export interface CaptureState {
  enabled: boolean;
  marker: string;
  marker_present: boolean;
  env_off: boolean;
  source: "env" | "marker" | "on";
}

export interface Neo4jStatus {
  url: string;
  db: string;
  user: string;
  /** The password itself is NEVER sent to the browser. */
  password_set: boolean;
  configured: boolean;
  up: boolean;
  reading_from: string;
  file: string;
  env: string[];
}

export interface Status extends Envelope {
  writable: boolean;
  boot: string;
  db: string;
  home: string;
  nodes: number;
  edges: number;
  pending_ops: number;
  loose_captures: number;
  neo4j: Neo4jStatus;
}

export interface Timezones extends Envelope {
  zones: string[];
}

/** One person node in a duplicate group, with how much history it carries. */
export interface DuplicateCandidate {
  id: string;
  title: string;
  edges: number;
  /** How sure the name match is. Only on the duplicates, not the canonical. */
  confidence?: "certain" | "likely";
}

/**
 * A group of person nodes that look like one human.
 *
 * The canonical is the most connected node, because merging into it moves the
 * fewest edges. Detected by name matching over every alias — no model call.
 */
export interface PersonDuplicate {
  canonical: DuplicateCandidate;
  duplicates: DuplicateCandidate[];
}

export interface PersonDuplicates extends Envelope {
  duplicates: PersonDuplicate[];
}

// ---------------------------------------------------------------------------
// Upkeep: consolidation, its preview, and the snapshots it leaves
// ---------------------------------------------------------------------------

/** What one day's consolidation would do. Computed by the engine, not guessed. */
export interface ConsolidationDay {
  day: string;
  digest: string;
  digest_exists: boolean;
  action: "create" | "update";
  captures: number;
  prompts: number;
  newly_consolidated: number;
  already_consolidated: number;
  chars_behind_it: number;
}

export interface ConsolidationPreview extends Envelope {
  dry_run: true;
  keep_days: number;
  cutoff: string;
  still_loose: number;
  backup_keep_days: number;
  days: ConsolidationDay[];
  totals: {
    captures: number;
    new_digests: number;
    updated_digests: number;
    newly_consolidated: number;
    chars: number;
  };
}

export interface Backup {
  name: string;
  path: string;
  bytes: number;
  created: string;
  expires: string;
  expired: boolean;
}

export interface BackupList extends Envelope {
  dir: string;
  keep_days: number;
  backups: Backup[];
}

export interface MaintainResult extends Envelope {
  keep_days?: number;
  cutoff?: string;
  days_consolidated?: number;
  newly_consolidated?: number;
  still_loose?: number;
  read_reduction?: string;
  backup?: { path: string; bytes: number } | null;
  /** Set when the write was refused for want of an explicit confirmation. */
  needs_confirmation?: boolean;
}

// ---------------------------------------------------------------------------
// Doctor — the health check, in the browser
// ---------------------------------------------------------------------------

export interface DoctorReport extends Envelope {
  /** The verdict. Distinct from `ok`, which only says the call succeeded. */
  healthy: boolean;
  problems: string[];
  status: Record<string, unknown>;
  paths: Record<string, unknown>;
  config: Record<string, unknown>;
  content: Record<string, unknown>;
  hooks: Record<string, unknown>;
  mirror: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// UI preferences — the browser's own state, optionally kept in the brain
// ---------------------------------------------------------------------------

/** An opaque blob: the shape belongs to the frontend, and the server stores it
 *  without opinions so a new toggle needs no server change. */
export type UiPrefs = Record<string, unknown>;

export interface UiPrefsPayload extends Envelope {
  synced: boolean;
  path: string;
  prefs: UiPrefs;
}
