import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { DashboardSection } from "./DashboardSection";
import { renderApp } from "@/test/utils";
import { failWith, resetWrites, server, writes } from "@/test/server";

beforeEach(resetWrites);

const openComposer = async () => {
  const { user } = renderApp(<DashboardSection />, { route: "/dashboard" });
  await screen.findByText("Agenda — 2 items");
  await user.click(screen.getByRole("button", { name: /New task/ }));
  return user;
};

describe("the new-task composer", () => {
  it("refuses a task with no title, before the round trip", async () => {
    const user = await openComposer();
    await user.click(screen.getByRole("button", { name: "Create task" }));
    expect(await screen.findByText("title required")).toBeInTheDocument();
    expect(writes.filter((w) => w.path === "/api/add-task")).toHaveLength(0);
  });

  it("creates a task with the fields the engine documents", async () => {
    const user = await openComposer();
    await user.type(screen.getByLabelText(/^title/i), "Write the release notes");
    await user.type(screen.getByLabelText(/^due/i), "2026-10-05");
    await user.selectOptions(screen.getByLabelText(/^priority/i), "P2");
    await user.click(screen.getByRole("button", { name: "Create task" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/add-task")?.body).toMatchObject({
        title: "Write the release notes",
        due: "2026-10-05T12:00:00",
        priority: "P2",
        type: "task",
      }),
    );
  });

  it("offers only the two types the engine accepts here", async () => {
    await openComposer();
    const select = screen.getByLabelText(/^type/i) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["task", "subtask"]);
  });

  it("recognises what the title mentions and shows why", async () => {
    const user = await openComposer();
    await user.type(screen.getByLabelText(/^title/i), "pair with alex on the map");
    expect(await screen.findByText(/recognised 1 connection/)).toBeInTheDocument();
    expect(screen.getByText(/matched “alex”/)).toBeInTheDocument();
  });

  it("sends the recognised links, each with its relation", async () => {
    const user = await openComposer();
    await user.type(screen.getByLabelText(/^title/i), "pair with alex");
    await screen.findByText(/recognised 1 connection/);
    await user.click(screen.getByRole("button", { name: "Create task" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/add-task")?.body).toMatchObject({
        links: [{ id: "person_alex", rel: "mentions" }],
      }),
    );
  });

  it("drops a suggestion that was unchecked", async () => {
    const user = await openComposer();
    await user.type(screen.getByLabelText(/^title/i), "pair with alex");
    await screen.findByText(/recognised 1 connection/);
    await user.click(screen.getByRole("checkbox", { name: /Alex/ }));
    await user.click(screen.getByRole("button", { name: "Create task" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/add-task")?.body).toMatchObject({ links: [] }),
    );
  });

  it("keeps a relation the person re-pointed, even as they keep typing", async () => {
    const user = await openComposer();
    const title = screen.getByLabelText(/^title/i);
    await user.type(title, "pair with alex");
    await screen.findByText(/recognised 1 connection/);
    await user.selectOptions(screen.getByRole("combobox", { name: /relation to Alex/ }), "blocked-by");
    await user.type(title, " tomorrow");
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /relation to Alex/ })).toHaveValue("blocked-by"),
    );
  });

  it("offers the relations the engine enforces, never a list of its own", async () => {
    const user = await openComposer();
    await user.type(screen.getByLabelText(/^title/i), "pair with alex");
    const select = (await screen.findByRole("combobox", { name: /relation to Alex/ })) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual([
      "relates-to",
      "blocked-by",
      "blocks",
      "part-of",
      "mentions",
      "caused-by",
    ]);
  });

  it("creates in the realm the filter narrowed to", async () => {
    const { user } = renderApp(<DashboardSection />, { route: "/dashboard" });
    await screen.findByText("Agenda — 2 items");
    await user.click(screen.getByRole("button", { name: /^personal \(/ }));
    await user.click(screen.getByRole("button", { name: /New task/ }));
    expect(screen.getByLabelText(/^realm/i)).toHaveValue("personal");
  });

  it("does not take the form down when recognition fails", async () => {
    server.use(http.get("/api/suggest-links", () => HttpResponse.json({ ok: false, error: "boom" }, { status: 500 })));
    const user = await openComposer();
    await user.type(screen.getByLabelText(/^title/i), "pair with alex");
    expect(screen.getByRole("button", { name: "Create task" })).toBeEnabled();
  });

  it("reports a refused create with the server's reason and keeps the draft", async () => {
    failWith("post", "/api/add-task", 400, "type must be task or subtask");
    const user = await openComposer();
    await user.type(screen.getByLabelText(/^title/i), "Something");
    await user.click(screen.getByRole("button", { name: "Create task" }));
    expect(await screen.findByText("type must be task or subtask")).toBeInTheDocument();
    expect(screen.getByLabelText(/^title/i)).toHaveValue("Something");
  });
});
