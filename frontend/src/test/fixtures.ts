/**
 * One brain, shared by every test.
 *
 * The shapes here are the shapes `serve.py` actually sends — they were read off
 * `brain_query._slim`, `graph.skill_trees` and `settings_payload`, and
 * `tests/test_openapi.py` keeps the spec those came from honest.
 */
import type {
  BackupList,
  CaptureState,
  ConsolidationPreview,
  DoctorReport,
  GraphPayload,
  Meta,
  NodeDetail,
  PersonDuplicates,
  Settings,
  SkillsPayload,
  Status,
  ViewPayload,
} from "@/api/types";

export const metaFixture: Meta = {
  ok: true,
  rels: ["relates-to", "blocked-by", "blocks", "part-of", "mentions", "caused-by"],
  statuses: ["open", "doing", "blocked", "waiting", "done", "cancelled"],
  realms: {
    optibus: { nodes: 42, open_tasks: 7 },
    personal: { nodes: 11, open_tasks: 2 },
  },
  default_realm: "optibus",
  kinds: {
    "(none)": { nodes: 50, open_tasks: 8 },
    postmortem: { nodes: 3, open_tasks: 1 },
  },
  no_kind_token: "(none)",
  types: { task: 9, person: 4, note: 6 },
  skill_sections: [
    { field: "when_to_use", heading: "## When to use", required: true, generated: false },
    { field: "given", heading: "## Given", required: true, generated: false },
    { field: "rules_honored", heading: "## Rules honored", required: false, generated: true },
    { field: "depends_on", heading: "## Depends on", required: false, generated: true },
    { field: "deliverables", heading: "## Deliverables", required: true, generated: false },
    { field: "judged_against", heading: "## Judged against", required: true, generated: false },
    { field: "guardrails", heading: "## Guardrails", required: true, generated: false },
    { field: "reply", heading: "## Reply", required: true, generated: false },
  ],
  rule_sections: [
    { field: "the_rule", heading: "THE RULE", required: true },
    { field: "why", heading: "WHY", required: false },
    { field: "how_to_apply", heading: "HOW TO APPLY", required: false },
    { field: "exceptions", heading: "EXCEPTIONS", required: false },
    { field: "judged_against", heading: "JUDGED AGAINST", required: true },
    { field: "related", heading: "RELATED", required: false },
  ],
  decl_labels: [
    { field: "requires", label: "Requires" },
    { field: "waits_for", label: "Waits for" },
    { field: "enriched_by", label: "Enriched by" },
    { field: "reads", label: "Reads" },
    { field: "writes", label: "Writes" },
  ],
  rule_sources: ["plugin", "user", "learned"],
  node_types: ["task", "person", "note", "decision", "skill"],
  writable: true,
  boot: "1234-1700000000",
};

export const graphFixture: GraphPayload = {
  ok: true,
  nodes: [
    { id: "task_ship-ui_a1", title: "Ship the new UI", type: "task", realm: "optibus", status: "open", degree: 2, kind: "(none)" },
    { id: "person_gabriel", title: "Gabriel", type: "person", realm: "optibus", degree: 3, kind: "(none)" },
    { id: "note_retro_b2", title: "Retro notes", type: "note", realm: "personal", degree: 1, kind: "postmortem" },
    { id: "task_done_c3", title: "Old finished thing", type: "task", realm: "optibus", status: "done", degree: 1, kind: "(none)" },
  ],
  edges: [
    { src: "person_gabriel", dst: "task_ship-ui_a1", rel: "relates-to" },
    { src: "task_ship-ui_a1", dst: "note_retro_b2", rel: "mentions" },
    { src: "person_gabriel", dst: "task_done_c3", rel: "relates-to" },
  ],
  meta: {
    count: 4,
    edges: 3,
    by_type: { task: 2, person: 1, note: 1 },
    by_realm: { optibus: 3, personal: 1 },
    by_kind: { "(none)": 3, postmortem: 1 },
    boot: "1234-1700000000",
    backend: "sqlite",
  },
};

export const agendaFixture: ViewPayload = {
  ok: true,
  view: "agenda",
  title: "Agenda",
  ref: "2026-09-22",
  count: 2,
  results: [
    {
      id: "task_ship-ui_a1",
      title: "Ship the new UI",
      type: "task",
      status: "open",
      realm: "optibus",
      due: "2026-09-20T12:00:00",
      due_local: "2026-09-20",
      assignee: "gabriel",
      kind: "(none)",
      mine: true,
      blocked_by: [{ id: "task_review_d4", title: "Review it" }],
      blocks: [],
      related: [],
    },
    {
      id: "task_write-docs_e5",
      title: "Write the docs",
      type: "task",
      status: "doing",
      realm: "optibus",
      due: "",
      due_local: "--",
      assignee: "",
      kind: "(none)",
      mine: false,
      blocked_by: [],
      blocks: [],
      related: [],
    },
  ],
};

export const nodeDetailFixture: NodeDetail = {
  ok: true,
  id: "task_ship-ui_a1",
  type: "task",
  title: "Ship the new UI",
  body: "The whole viz, as a real app.",
  status: "open",
  realm: "optibus",
  due: "2026-09-20T12:00:00",
  props: { assignee: "gabriel", priority: "P2" },
  edges: [
    { dir: "in", rel: "relates-to", id: "person_gabriel", title: "Gabriel", type: "person" },
    { dir: "out", rel: "mentions", id: "note_retro_b2", title: "Retro notes", type: "note" },
  ],
};

/**
 * A PERSON node with its neighbourhood — what `/api/node?id=person_…` answers.
 *
 * Separate from nodeDetailFixture, which is a task: a person's card is about
 * what they are involved in, and reusing a task's edges made the test assert on
 * a node that happened to share the person's name.
 */
export const personDetailFixture: NodeDetail = {
  ok: true,
  id: "person_gabriel",
  type: "person",
  title: "Gabriel",
  body: "",
  realm: "optibus",
  props: { aliases: ["gabe"] },
  edges: [
    { dir: "in", rel: "assigned-to", id: "task_ship-ui_a1", title: "Ship the new UI", type: "task" },
    { dir: "in", rel: "assigned-to", id: "task_docs_c3", title: "Write the docs", type: "task" },
    { dir: "in", rel: "mentions", id: "note_retro_b2", title: "Retro notes", type: "note" },
  ],
};

export const skillsFixture: SkillsPayload = {
  ok: true,
  trees: [
    {
      kind: "plugin",
      hub: "hub_skills_plugin",
      title: "Built-in skills",
      count: 2,
      skills: [
        {
          id: "skill_recall",
          title: "recall",
          description: "Read the graph back.",
          triggers: "what do I know about",
          kind: "plugin",
          uses: 12,
          requires: [],
          waits_for: [],
          enriched_by: ["skill_ingest-context"],
          reads: ["task", "person"],
          writes: [],
          reads_other: "",
          writes_other: "",
          domains: ["hub_process"],
          declared: true,
          rules: [
            { id: "rule_s2_the-memory-graph-api", title: "The memory graph API", why: "declared", non_negotiable: false },
            { id: "rule_s0_always-on-capture", title: "Always-on capture", why: "always", non_negotiable: true },
          ],
          children: [
            {
              id: "skill_visualize",
              title: "visualize",
              description: "Open the live brain UI.",
              triggers: "show me the graph",
              kind: "plugin",
              uses: 3,
              requires: ["skill_recall"],
              waits_for: [],
              enriched_by: [],
              reads: ["task"],
              writes: [],
              reads_other: "",
              writes_other: "",
              domains: ["hub_process"],
              declared: false,
              rules: [],
              children: [],
            },
          ],
        },
      ],
    },
    {
      kind: "learned",
      hub: "hub_skills_learned",
      title: "Learned skills",
      count: 0,
      skills: [],
    },
  ],
  rules: [
    {
      id: "rule_user_no-emoji",
      title: "Never use emoji",
      source: "user",
      section: "user",
      non_negotiable: true,
      scenario: "writing anything for a person",
      domains: ["hub_writing"],
      chars: 420,
    },
    {
      id: "rule_s2_the-memory-graph-api",
      title: "The memory graph API",
      source: "plugin",
      section: "2",
      non_negotiable: false,
      scenario: "reading or writing the graph",
      domains: ["hub_process"],
      chars: 900,
    },
  ],
  node_types: [
    { type: "task", description: "Something someone has to do.", count: 9, readers: ["skill_recall"], writers: [] },
    { type: "person", description: "A human.", count: 4, readers: ["skill_recall"], writers: [] },
  ],
  areas: { hub_process: "Process", hub_writing: "Writing" },
  realm: "optibus",
  processes: [
    {
      id: "process_epic-refinement_854d",
      title: "Epic-lead refinement process",
      body: "1. CATCH UP FIRST.\n2. Refine each child.",
      realm: "optibus",
      trigger: "refine the issues in my epics",
      established: "2026-09-09",
      category: "process",
      domains: ["hub_process"],
      edges: 3,
      updated: "2026-09-10T10:00:00+00:00",
    },
    {
      id: "process_weekly-review_aa11",
      title: "Weekly personal review",
      body: "Look back at the week.",
      realm: "personal",
      trigger: "sunday evening",
      established: "",
      category: "process",
      domains: ["hub_process"],
      edges: 1,
      updated: "2026-09-01T10:00:00+00:00",
    },
  ],
};

export const settingsFixture: Settings = {
  ok: true,
  writable: true,
  keys: [
    "tz",
    "realm",
    "owner",
    "backend",
    "autosync",
    "consolidate",
    "map_poll_seconds",
    "dash_refresh_seconds",
    "ui_sync",
  ],
  settings: { tz: "America/Sao_Paulo", realm: "optibus" },
  env: {},
  path: "/home/brain/settings.json",
  effective: {
    tz: "America/Sao_Paulo",
    tz_source: "settings",
    system_tz: "UTC",
    now: "2026-09-22T09:00:00-03:00",
    realm: "optibus",
    owner: "Gabriel",
    owner_source: "default",
    backend: "auto",
    backends: ["sqlite", "neo4j", "auto"],
    autosync: true,
    autosync_source: "default",
    consolidate: true,
    consolidate_source: "default",
    ui_sync: false,
    ui_sync_source: "default",
    map_poll_seconds: 2,
    dash_refresh_seconds: 120,
  },
  capture: {
    enabled: true,
    marker: "/home/brain/.disabled",
    marker_present: false,
    env_off: false,
    source: "on",
  },
};

export const captureFixture: CaptureState = settingsFixture.capture;

export const doctorFixture: DoctorReport = {
  ok: true,
  healthy: true,
  problems: [],
  status: { ok: true, enabled: true, disabled: false },
  paths: { ok: true, home: "/home/brain", db: "/home/brain/graph.db", db_exists: true },
  config: { ok: true, tz: "America/Sao_Paulo", realm: "optibus", owner: "Gabriel" },
  content: { ok: true, nodes: 4, open_loops: 2 },
  hooks: { ok: true, hooks_json: "/home/.claude/settings.json", events: {} },
  mirror: { ok: true, configured: false },
};

export const consolidationPreviewFixture: ConsolidationPreview = {
  ok: true,
  dry_run: true,
  keep_days: 2,
  cutoff: "2026-09-20",
  still_loose: 12,
  backup_keep_days: 5,
  days: [
    {
      day: "2026-09-19",
      digest: "note_digest_2026-09-19",
      digest_exists: false,
      action: "create",
      captures: 12,
      prompts: 6,
      newly_consolidated: 12,
      already_consolidated: 0,
      chars_behind_it: 4200,
    },
  ],
  totals: {
    captures: 12,
    new_digests: 1,
    updated_digests: 0,
    newly_consolidated: 12,
    chars: 4200,
  },
};

export const backupsFixture: BackupList = {
  ok: true,
  dir: "/home/brain/backups",
  keep_days: 5,
  backups: [
    {
      name: "maintain-20260922T120000Z.db",
      path: "/home/brain/backups/maintain-20260922T120000Z.db",
      bytes: 102400,
      created: "2026-09-22T12:00:00+00:00",
      expires: "2026-09-27T12:00:00+00:00",
      expired: false,
    },
  ],
};

export const personDuplicatesFixture: PersonDuplicates = {
  ok: true,
  duplicates: [
    {
      canonical: { id: "person_isadora", title: "Isadora", edges: 9 },
      duplicates: [{ id: "person_isa", title: "Isa", edges: 2, confidence: "likely" }],
    },
  ],
};

export const statusFixture: Status = {
  ok: true,
  writable: true,
  boot: "1234-1700000000",
  db: "/home/brain/graph.db",
  home: "/home/brain",
  nodes: 4,
  edges: 3,
  pending_ops: 0,
  loose_captures: 1,
  neo4j: {
    url: "",
    db: "neo4j",
    user: "neo4j",
    password_set: false,
    configured: false,
    up: false,
    reading_from: "sqlite (local mirror)",
    file: "/home/brain/neo4j.json",
    env: [],
  },
};
