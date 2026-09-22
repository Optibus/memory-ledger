import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import {
  agendaFixture,
  graphFixture,
  metaFixture,
  nodeDetailFixture,
  settingsFixture,
  skillsFixture,
  statusFixture,
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
  http.get("/api/assignees", () => ok({ assignees: { gabriel: 3, unassigned: 1 } })),
  http.get("/api/realms", () => ok({ realms: metaFixture.realms, default: "optibus" })),
  http.get("/api/kinds", () => ok({ kinds: metaFixture.kinds, none_token: "(none)" })),
  http.get("/api/people", () =>
    ok({
      people: [
        { id: "person_gabriel", title: "Gabriel", aliases: ["gabe"], realm: "optibus" },
        { id: "person_isadora", title: "Isadora", aliases: ["isa"], realm: "optibus" },
      ],
    }),
  ),
  http.get("/api/node", () => HttpResponse.json(nodeDetailFixture)),
  http.get("/api/skills", () => HttpResponse.json(skillsFixture)),
  http.get("/api/rule", ({ request }) => {
    const id = new URL(request.url).searchParams.get("id");
    return ok({ rule: { id, title: "a rule", body: "THE RULE\n\nDo the thing." } });
  }),
  http.get("/api/settings", () => HttpResponse.json(settingsFixture)),
  http.get("/api/status", () => HttpResponse.json(statusFixture)),
  http.get("/api/timezones", () => ok({ zones: ["UTC", "America/Sao_Paulo", "Europe/Lisbon"] })),
  http.get("/api/search", ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") || "").toLowerCase();
    const hits = graphFixture.nodes
      .filter((n) => n.title.toLowerCase().includes(q))
      .map((n) => ({ id: n.id, title: n.title, type: n.type, realm: n.realm }));
    return ok({ results: hits });
  }),
  http.get("/api/suggest-links", ({ request }) => {
    const text = (new URL(request.url).searchParams.get("text") || "").toLowerCase();
    const suggestions = text.includes("gabriel")
      ? [
          {
            id: "person_gabriel",
            title: "Gabriel",
            type: "person",
            rel: "mentions",
            cue: "gabriel",
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
  write("/api/settings"),
  write("/api/neo4j-config"),
  write("/api/neo4j-test"),
  write("/api/flush"),
  write("/api/prune"),
  write("/api/create-rule"),
  write("/api/create-skill"),
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
  write("/api/delete-contracts", () => ok({ count: 1, deleted: [{ id: "rule_x", type: "rule" }], refused: [], not_found: [] })),
];

export const server = setupServer(...handlers);

/** Makes one endpoint fail, for the "what does the UI say when it breaks" tests. */
export function failWith(method: "get" | "post", path: string, status: number, error: string) {
  server.use(
    http[method](path, () => HttpResponse.json({ ok: false, error }, { status })),
  );
}
