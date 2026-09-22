import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { SkillsSection } from "./SkillsSection";
import { renderApp } from "@/test/utils";
import { failWith, resetWrites, server, writes } from "@/test/server";
import { skillsFixture } from "@/test/fixtures";

beforeEach(resetWrites);

describe("Skills", () => {
  it("shows both trees with their counts", async () => {
    renderApp(<SkillsSection />, { route: "/skills" });
    expect(await screen.findByRole("heading", { name: /Built-in skills/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Learned skills/ })).toBeInTheDocument();
    expect(screen.getByText("1 process · 2 skills · 2 rules")).toBeInTheDocument();
  });

  it("nests a skill under the one it requires, and nothing else", async () => {
    renderApp(<SkillsSection />, { route: "/skills" });
    const child = await screen.findByRole("button", { name: /visualize/ });
    const parent = screen.getByRole("button", { name: /recall/ });
    // `visualize` requires `recall`, so it sits inside the parent's branch.
    expect(child.closest(".ml-skills__kid")).not.toBeNull();
    expect(parent.closest(".ml-skills__kid")).toBeNull();
  });

  it("opens a skill and shows its graph surface and dependencies", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await user.click(await screen.findByRole("button", { name: /recall/ }));
    expect(await screen.findByText("Graph surface")).toBeInTheDocument();
    expect(screen.getByText("reads task")).toBeInTheDocument();
    expect(screen.getByText("enriched by ingest-context")).toBeInTheDocument();
  });

  it("separates the rules a skill actually pulled from the always-on ones", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await user.click(await screen.findByRole("button", { name: /recall/ }));
    expect(await screen.findByText("declared")).toBeInTheDocument();
    // A non-negotiable is on every skill, so it is folded away by default.
    expect(screen.queryByText("always on")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /show 1 always-on rules/ }));
    expect(await screen.findByText("always on")).toBeInTheDocument();
  });

  it("reads a rule in full, from the catalogue", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await user.click(await screen.findByRole("button", { name: /Never use emoji/ }));
    expect(await screen.findByText(/THE RULE/)).toBeInTheDocument();
  });

  it("shows the neighbours one level deep, from the same endpoint the dashboard uses", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await user.click(await screen.findByRole("button", { name: /recall/ }));
    expect(await screen.findByText("Points at")).toBeInTheDocument();
    expect(screen.getByText("Pointed at by")).toBeInTheDocument();
  });

  it("filters both corpora from one text box", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.type(screen.getByRole("searchbox"), "emoji");
    await waitFor(() => expect(screen.getByText("0 processes · 0 skills · 1 rule")).toBeInTheDocument());
  });

  it("keeps a parent whose descendant matched, so filtering never orphans a child", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.type(screen.getByRole("searchbox"), "visualize");
    // `recall` does not match, but it has to stay or its child would hang from nothing.
    expect(await screen.findByRole("button", { name: /recall/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /visualize/ })).toBeInTheDocument();
  });

  it("narrows by type, and the types are multi-select", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: /Types/ }));
    await user.click(screen.getByRole("checkbox", { name: /Skills/ }));
    expect(await screen.findByText("1 process · 2 rules")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Built-in skills/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /Processes/ }));
    expect(await screen.findByText("2 rules")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^Processes/ })).not.toBeInTheDocument();
  });

  it("filters rules by source", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: /Sources/ }));
    await user.click(screen.getByRole("checkbox", { name: /Built-in/ }));
    await user.click(screen.getByRole("checkbox", { name: /Learned/ }));
    await waitFor(() => expect(screen.getByText("1 process · 0 skills · 1 rule")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /The memory graph API/ })).not.toBeInTheDocument();
  });

  it("takes several sources at once", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: /Sources/ }));
    await user.click(screen.getByRole("button", { name: "none" }));
    await waitFor(() => expect(screen.getByText("0 processes · 0 skills · 0 rules")).toBeInTheDocument());
    await user.click(screen.getByRole("checkbox", { name: /Built-in/ }));
    await user.click(screen.getByRole("checkbox", { name: /User/ }));
    await waitFor(() => expect(screen.getByText("1 process · 2 skills · 2 rules")).toBeInTheDocument());
  });

  it("labels the domain filter with the area titles rather than hub ids", async () => {
    renderApp(<SkillsSection />, { route: "/skills" });
    const select = await screen.findByRole("combobox", { name: "domain" });
    expect(select).toHaveTextContent("Process");
    expect(select).toHaveTextContent("Writing");
  });

  it("tells you what to run when the trees were never built", async () => {
    failWith("get", "/api/skills", 500, "no skill nodes");
    renderApp(<SkillsSection />, { route: "/skills" });
    expect(await screen.findByText(/sync-skill-tree/)).toBeInTheDocument();
  });
});

describe("Skills selection mode", () => {
  const enterSelection = async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: /Options/ }));
    await user.click(screen.getByRole("button", { name: /Select processes, skills and rules/ }));
    return user;
  };

  it("puts checkboxes on every card and says nothing is selected yet", async () => {
    await enterSelection();
    expect(await screen.findByText("Nothing selected")).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);
  });

  it("selects everything currently shown, not everything that exists", async () => {
    const user = await enterSelection();
    await user.click(screen.getByRole("button", { name: "Select all shown" }));
    expect(await screen.findByText("5 selected")).toBeInTheDocument();
  });

  it("exports the selection split into skills and rules", async () => {
    const user = await enterSelection();
    await user.click(screen.getByRole("button", { name: "Select all shown" }));
    await user.click(screen.getByRole("button", { name: /Export selected/ }));
    await waitFor(() => {
      const body = writes.find((w) => w.path === "/api/export-bundle")?.body as {
        skills: string[];
        rules: string[];
      };
      expect(body.skills.sort()).toEqual(["skill_recall", "skill_visualize"]);
      expect(body.rules).toHaveLength(2);
      expect((body as unknown as { processes: string[] }).processes).toEqual(["process_epic-refinement_854d"]);
    });
    expect(await screen.findByText(/Bundled 1 skill and 1 rule into/)).toBeInTheDocument();
  });

  it("asks before deleting, and says a built-in will come straight back", async () => {
    const user = await enterSelection();
    await user.click(screen.getByRole("button", { name: "Select all shown" }));
    await user.click(screen.getByRole("button", { name: /Delete selected/ }));
    expect(await screen.findByRole("dialog", { name: /Delete 5 items\?/ })).toBeInTheDocument();
    expect(screen.getByText(/cannot be undone from here/)).toBeInTheDocument();
    expect(screen.getByText(/the next ingest brings it straight back/)).toBeInTheDocument();
    expect(writes.filter((w) => w.path === "/api/delete-contracts")).toHaveLength(0);
  });

  it("only forces built-ins when the person says so", async () => {
    const user = await enterSelection();
    await user.click(screen.getByRole("button", { name: "Select all shown" }));
    await user.click(screen.getByRole("button", { name: /Delete selected/ }));
    await user.click(screen.getByRole("button", { name: "Delete built-ins too" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect((writes.find((w) => w.path === "/api/delete-contracts")?.body as { force: boolean }).force).toBe(true),
    );
  });

  it("drops the selection when selection mode is left", async () => {
    const user = await enterSelection();
    await user.click(screen.getByRole("button", { name: "Select all shown" }));
    await screen.findByText("5 selected");
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("5 selected")).not.toBeInTheDocument();
  });
});

describe("Skills bundles and markdown sync", () => {
  it("stages an upload, asks what to do, and writes nothing until confirmed", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["zip"], "team.zip", { type: "application/zip" }));

    expect(await screen.findByRole("dialog", { name: /Import from team.zip/ })).toBeInTheDocument();
    expect(writes.filter((w) => w.path === "/api/import-bundle")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/import-bundle")?.body).toEqual({
        token: "tok-1",
        select: { skill_new: "update" },
      }),
    );
  });

  it("says nothing was written when the import is cancelled", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["zip"], "team.zip", { type: "application/zip" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Import cancelled. Nothing was written.")).toBeInTheDocument();
  });

  it("asks for a directory and reports where the export landed", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: /Options/ }));
    await user.click(screen.getByRole("button", { name: /Export the learned tree/ }));
    expect(await screen.findByRole("dialog", { name: /Export the learned tree/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Export" }));
    expect(await screen.findByText(/learned_skills/)).toBeInTheDocument();
  });

  it("reports a refused sync instead of failing silently", async () => {
    server.use(
      http.post("/api/ingest-skills", () =>
        HttpResponse.json({ ok: false, error: "no such directory" }, { status: 400 }),
      ),
    );
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: /Options/ }));
    await user.click(screen.getByRole("button", { name: /Ingest a .md folder/ }));
    await user.click(await screen.findByRole("button", { name: "Ingest" }));
    expect(await screen.findByText(/Ingesting failed: no such directory/)).toBeInTheDocument();
  });
});

describe("Skills with an empty brain", () => {
  it("says how to build the tree rather than showing a blank page", async () => {
    server.use(
      http.get("/api/skills", () =>
        HttpResponse.json({ ...skillsFixture, trees: [{ ...skillsFixture.trees[0]!, count: 0, skills: [] }], rules: [] }),
      ),
    );
    renderApp(<SkillsSection />, { route: "/skills" });
    expect(await screen.findByText(/No skills in this tree yet/)).toBeInTheDocument();
  });
});

describe("Skills processes", () => {
  it("shows only the current realm's processes until another realm is picked", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    expect(await screen.findByRole("button", { name: /Epic-lead refinement process/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Weekly personal review/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Realms/ }));
    await user.click(screen.getByRole("checkbox", { name: /personal/ }));
    expect(await screen.findByRole("button", { name: /Weekly personal review/ })).toBeInTheDocument();
    expect(screen.getByText("2 processes · 2 skills · 2 rules")).toBeInTheDocument();
  });

  it("opens a process and reads its steps", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await user.click(await screen.findByRole("button", { name: /Epic-lead refinement process/ }));
    expect(await screen.findByText(/CATCH UP FIRST/)).toBeInTheDocument();
    expect(screen.getByText(/established 2026-09-09/)).toBeInTheDocument();
  });

  it("finds a process by the words in its steps", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.type(screen.getByRole("searchbox"), "catch up");
    await waitFor(() => expect(screen.getByText("1 process · 0 skills · 0 rules")).toBeInTheDocument());
  });

  it("offers the brain's realms as a dropdown, and stores in the one picked", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: "New process…" }));
    await user.type(await screen.findByRole("textbox", { name: /Title/ }), "Sunday review");
    const realm = screen.getByRole("combobox", { name: /Realm/ });
    expect(realm).toHaveValue("optibus");
    await user.selectOptions(realm, "personal");
    await user.click(screen.getByRole("button", { name: "Save process" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/create-process")?.body).toMatchObject({ realm: "personal" }),
    );
  });

  it("stores a new process in the current realm by default", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: "New process…" }));
    const dialog = await screen.findByRole("dialog", { name: /New process/ });
    expect(dialog).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: /Title/ }), "Ship a hotfix");
    await user.click(screen.getByRole("button", { name: "Save process" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/create-process")?.body).toEqual({
        title: "Ship a hotfix",
        trigger: "",
        body: "",
        realm: "optibus",
      }),
    );
    expect(await screen.findByText(/in the optibus realm/)).toBeInTheDocument();
  });

  it("asks which realm a bundle's processes land in, defaulting to the current one", async () => {
    server.use(
      http.post("/api/upload-bundle", () =>
        HttpResponse.json({
          ok: true,
          token: "tok-2",
          counts: { processes: 1 },
          items: [{ id: "process_theirs_01", title: "Their process", kind: "process", realm: "team-b", exists: false }],
        }),
      ),
    );
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("1 process · 2 skills · 2 rules");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["zip"], "mate.zip", { type: "application/zip" }));
    const realm = await screen.findByRole("combobox", { name: "process realm" });
    expect(realm).toHaveValue("optibus");
    expect(screen.getByText("from team-b")).toBeInTheDocument();
    await user.selectOptions(realm, "personal");
    await user.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/import-bundle")?.body).toEqual({
        token: "tok-2",
        select: { process_theirs_01: "update" },
        process_realm: "personal",
      }),
    );
  });
});
