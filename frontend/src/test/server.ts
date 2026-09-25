import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import {
  agendaFixture,
  backupsFixture,
  captureFixture,
  consolidationPreviewFixture,
  doctorFixture,
  graphFixture,
  metaFixture,
  nodeDetailFixture,
  personDetailFixture,
  personDuplicatesFixture,
  settingsFixture,
  scriptsFixture,
  skillsFixture,
  statusFixture,
  triggerFixture,
  tasksFixture,
  taskDetailFixture,
  usageReportFixture,
} from "./fixtures";

/** Every write the tests observed, so a test can assert what was actually sent. */
export const writes: { path: string; body: unknown }[] = [];

export function resetWrites(): void {
  writes.length = 0;
}

const ok = (body: Record<string, unknown> = {}) => HttpResponse.json({ ok: true, ...body });

/** Records a write and answers ok, which is what most of them do. */
function write(path: string, respond: (body: never) => Response = () => ok()) {
  return http.post(path, async ({ request }) => {
    const body = await request.json().catch(() => ({}));
    writes.push({ path, body });
    return respond(body as never);
  });
}

export const handlers = [
  http.get("/api/meta", () => HttpResponse.json(metaFixture)),
  http.get("/api/graph", () => HttpResponse.json(graphFixture)),
  http.get("/api/views", () =>
    ok({ views: ["agenda", "open", "today", "tomorrow", "week", "closed", "range"] }),
  ),
  http.get("/api/assignees", () => ok({ assignees: { alex: 3, unassigned: 1 } })),
  write("/api/rename-realm", (b: { old: string; new: string }) =>
    ok({ from: b.old, to: b.new, moved: 42, merged_into_existing: b.new === "personal", default: b.new }),
  ),
  http.get("/api/realms", () => ok({ realms: metaFixture.realms, default: "work" })),
  http.get("/api/kinds", () => ok({ kinds: metaFixture.kinds, none_token: "(none)" })),
  http.get("/api/people", () =>
    ok({
      people: [
        { id: "person_alex", title: "Alex", aliases: ["gabe"], realm: "work" },
        { id: "person_maria", title: "Maria", aliases: ["mari"], realm: "work" },
      ],
    }),
  ),
  // A person id answers with a person; anything else with the task fixture.
  // One endpoint returning a task for `?id=person_…` is how a test ends up
  // asserting on a node that merely shares someone's name.
  http.get("/api/node", ({ request }) => {
    const id = new URL(request.url).searchParams.get("id") || "";
    return HttpResponse.json(id.startsWith("person_") ? personDetailFixture : nodeDetailFixture);
  }),
  http.get("/api/skills", () => HttpResponse.json(skillsFixture)),
  http.get("/api/scripts", () => HttpResponse.json(scriptsFixture)),
  http.get("/api/script", ({ request }) => {
    const name = new URL(request.url).searchParams.get("name") || "";
    const entry = scriptsFixture.scripts.find((x) => x.name === name);
    return entry
      ? ok({ script: { ...entry, text: `#!/bin/sh\n# ${entry.summary}\necho ${name}\n` } })
      : HttpResponse.json({ ok: false, error: "no such script" });
  }),
  http.get("/api/rule", ({ request }) => {
    const id = new URL(request.url).searchParams.get("id");
    return ok({ rule: { id, title: "a rule", body: "THE RULE\n\nDo the thing." } });
  }),
  http.get("/api/settings", () => HttpResponse.json(settingsFixture)),
  http.get("/api/status", () => HttpResponse.json(statusFixture)),
  http.get("/api/trigger", () => HttpResponse.json(triggerFixture)),
  http.get("/api/tasks", () => HttpResponse.json(tasksFixture)),
  http.get("/api/task", () => HttpResponse.json(taskDetailFixture)),
  http.get("/api/usage-report", ({ request }) => {
    const q = new URL(request.url).searchParams;
    return HttpResponse.json(usageReportFixture(q.get("from") || "", q.get("to") || ""));
  }),
  http.get("/api/timezones", () => ok({ zones: ["UTC", "America/Sao_Paulo", "Europe/Lisbon"] })),
  http.get("/api/capture", () => HttpResponse.json(captureFixture)),
  http.get("/api/doctor", () => HttpResponse.json(doctorFixture)),
  http.get("/api/backups", () => HttpResponse.json(backupsFixture)),
  http.get("/api/consolidate-preview", () => HttpResponse.json(consolidationPreviewFixture)),
  http.get("/api/person-duplicates", () => HttpResponse.json(personDuplicatesFixture)),
  // Nothing stored by default: the brain's copy is opt-in, and a test that did
  // not ask for synced preferences must see the same defaults a fresh browser
  // does. A test that wants one overrides this with server.use().
  http.get("/api/ui-prefs", () => ok({ synced: false, path: "/home/brain/ui_prefs.json", prefs: {} })),
  http.get("/api/search", ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") || "").toLowerCase();
    const hits = graphFixture.nodes
      .filter((n) => n.title.toLowerCase().includes(q))
      .map((n) => ({ id: n.id, title: n.title, type: n.type, realm: n.realm }));
    return ok({ results: hits });
  }),
  http.get("/api/suggest-links", ({ request }) => {
    const text = (new URL(request.url).searchParams.get("text") || "").toLowerCase();
    const suggestions = text.includes("alex")
      ? [
          {
            id: "person_alex",
            title: "Alex",
            type: "person",
            rel: "mentions",
            cue: "alex",
          },
        ]
      : [];
    return ok({ count: suggestions.length, suggestions });
  }),

  // the date views all answer from one fixture
  ...["agenda", "open", "today", "tomorrow", "week", "closed", "range"].map((v) =>
    http.get(`/api/${v}`, () => HttpResponse.json({ ...agendaFixture, view: v })),
  ),

  write("/api/add-task"),
  write("/api/set-due"),
  write("/api/complete"),
  write("/api/set-status"),
  write("/api/set-assignee"),
  write("/api/update-node"),
  write("/api/assign"),
  write("/api/link"),
  write("/api/unlink"),
  write("/api/propose-idea", (body: { title?: string; detail?: string; realm?: string }) =>
    ok({
      node: {
        id: "idea_new_0001", type: "idea", title: body.title, body: body.detail ?? "",
        status: "proposed", realm: body.realm || "work",
      },
    }),
  ),
  write("/api/settings"),
  write("/api/neo4j-config"),
  write("/api/neo4j-test"),
  write("/api/trigger-config", () => ok({ config: triggerFixture.config, runtime: triggerFixture.runtime })),
  write("/api/trigger-channel", () => ok({ channels: triggerFixture.channels })),
  write("/api/trigger-check", (body: { only?: string }) =>
    ok({ ready: true, target: body.only || "all", problems: [], notes: [`${body.only}: fine`] }),
  ),
  write("/api/flush"),
  write("/api/prune", () =>
    ok({ keep_days: 2, days_consolidated: 1, newly_consolidated: 12, still_loose: 0,
         backup: { path: "/home/brain/backups/maintain-20260922T120000Z.db", bytes: 102400 } }),
  ),
  write("/api/consolidate"),
  write("/api/capture", () => ok({ capture: { ...captureFixture, enabled: false, marker_present: true, source: "marker" } })),
  write("/api/ui-prefs", () => ok({ synced: true })),
  write("/api/backup", () => ok({ created: backupsFixture.backups[0], removed: [], backups: backupsFixture.backups })),
  write("/api/merge-person", () => ok({ moved: 2, dropped: 0 })),
  write("/api/add-alias", () => ok({ added: true })),
  write("/api/create-rule"),
  write("/api/create-skill"),
  write("/api/create-process", () =>
    ok({ node: { id: "process_new_0001", realm: "work" }, action: "created" }),
  ),
  write("/api/export-skills", () => ok({ dir: "/home/brain/learned_skills", skills: 2, rules: 1 })),
  write("/api/ingest-skills", () => ok({ count: 2, ingested: [], rules: [] })),
  write("/api/export-bundle", () =>
    ok({ skills: 1, rules: 1, path: "/home/brain/bundles/b.zip", name: "b.zip", download: "/api/bundle?name=b.zip" }),
  ),
  write("/api/upload-bundle", () =>
    ok({
      token: "tok-1",
      counts: { skills: 1, rules: 0 },
      items: [{ id: "skill_new", title: "new skill", kind: "skill", exists: false }],
    }),
  ),
  write("/api/import-bundle", () => ok({ applied: [{ id: "skill_new", action: "created" }] })),
  write("/api/export-scripts", () =>
    ok({
      path: "/home/brain/bundles/scripts-1.zip",
      name: "scripts-1.zip",
      download: "/api/bundle?name=scripts-1.zip",
      count: 2,
      scripts: ["dev_env.py", "mithra_token.sh"],
    }),
  ),
  write("/api/create-script", () => ok({ name: "new.sh", action: "created" })),
  write("/api/upload-scripts", () =>
    ok({
      token: "tok-s",
      dir: "/home/brain/scripts",
      count: 2,
      items: [
        { ...scriptsFixture.scripts[0]!, exists: true, identical: false, existing_bytes: 100 },
        { ...scriptsFixture.scripts[1]!, name: "new.sh", exists: false },
      ],
    }),
  ),
  write("/api/import-scripts", () =>
    ok({
      dir: "/home/brain/scripts",
      applied: [
        { name: "dev_env.py", action: "kept" },
        { name: "new.sh", action: "created" },
      ],
      not_in_zip: [],
    }),
  ),
  write("/api/delete-contracts", () => ok({ count: 1, deleted: [{ id: "rule_x", type: "rule" }], refused: [], not_found: [] })),
];

export const server = setupServer(...handlers);

/** Makes one endpoint fail, for the "what does the UI say when it breaks" tests. */
export function failWith(method: "get" | "post", path: string, status: number, error: string) {
  server.use(
    http[method](path, () => HttpResponse.json({ ok: false, error }, { status })),
  );
}
