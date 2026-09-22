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
    expect(screen.getByText("2 skills · 2 rules")).toBeInTheDocument();
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
    await screen.findByText("2 skills · 2 rules");
    await user.type(screen.getByRole("searchbox"), "emoji");
    await waitFor(() => expect(screen.getByText("0 skills · 1 rule")).toBeInTheDocument());
  });

  it("keeps a parent whose descendant matched, so filtering never orphans a child", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("2 skills · 2 rules");
    await user.type(screen.getByRole("searchbox"), "visualize");
    // `recall` does not match, but it has to stay or its child would hang from nothing.
    expect(await screen.findByRole("button", { name: /recall/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /visualize/ })).toBeInTheDocument();
  });

  it("narrows to one corpus with the show pills", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: "Rules" }));
    expect(await screen.findByText("2 rules")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Built-in skills/ })).not.toBeInTheDocument();
  });

  it("filters rules by source", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: "User" }));
    await waitFor(() => expect(screen.getByText(/1 rule/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /The memory graph API/ })).not.toBeInTheDocument();
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
    await screen.findByText("2 skills · 2 rules");
    await user.click(screen.getByRole("button", { name: /Options/ }));
    await user.click(screen.getByRole("button", { name: /Select skills and rules/ }));
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
    expect(await screen.findByText("4 selected")).toBeInTheDocument();
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
    });
    expect(await screen.findByText(/Bundled 1 skill and 1 rule into/)).toBeInTheDocument();
  });

  it("asks before deleting, and says a built-in will come straight back", async () => {
    const user = await enterSelection();
    await user.click(screen.getByRole("button", { name: "Select all shown" }));
    await user.click(screen.getByRole("button", { name: /Delete selected/ }));
    expect(await screen.findByRole("dialog", { name: /Delete 4 items\?/ })).toBeInTheDocument();
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
    await screen.findByText("4 selected");
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("4 selected")).not.toBeInTheDocument();
  });
});

describe("Skills bundles and markdown sync", () => {
  it("stages an upload, asks what to do, and writes nothing until confirmed", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("2 skills · 2 rules");
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
    await screen.findByText("2 skills · 2 rules");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["zip"], "team.zip", { type: "application/zip" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Import cancelled. Nothing was written.")).toBeInTheDocument();
  });

  it("asks for a directory and reports where the export landed", async () => {
    const { user } = renderApp(<SkillsSection />, { route: "/skills" });
    await screen.findByText("2 skills · 2 rules");
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
    await screen.findByText("2 skills · 2 rules");
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
