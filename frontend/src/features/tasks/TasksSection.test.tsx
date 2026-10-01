import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { TasksSection } from "./TasksSection";
import { duration, filterTasks } from "./filter";
import { renderApp } from "@/test/utils";
import { resetWrites, server, writes } from "@/test/server";
import { tasksFixture } from "@/test/fixtures";

describe("Tasks", () => {
  it("lists every task as a card coloured by its status", async () => {
    renderApp(<TasksSection />, { route: "/tasks", withMeta: false });
    const list = await screen.findByRole("list", { name: "tasks" });
    const cards = within(list).getAllByRole("listitem");
    expect(cards).toHaveLength(4);
    expect(cards[0]).toHaveClass("ml-run--running");
    expect(cards[1]).toHaveClass("ml-run--done");
    expect(cards[2]).toHaveClass("ml-run--failed");
    expect(cards[3]).toHaveClass("ml-run--blocked");
  });

  it("offers to reprocess only a failed or blocked task, and runs a failed one at once", async () => {
    resetWrites();
    const { user } = renderApp(<TasksSection />, { route: "/tasks", withMeta: false });
    const list = await screen.findByRole("list", { name: "tasks" });
    const cards = await within(list).findAllByRole("listitem");
    expect(within(cards[0]).queryByRole("button", { name: /^Reprocess/ })).toBeNull();
    expect(within(cards[1]).queryByRole("button", { name: /^Reprocess/ })).toBeNull();
    await user.click(within(cards[2]).getByRole("button", { name: /^Reprocess/ }));
    await waitFor(() =>
      expect(writes.filter((w) => w.path === "/api/trigger-reprocess").map((w) => w.body)).toEqual([
        { request_id: "t-failed" },
      ]),
    );
  });

  it("asks before running a blocked message", async () => {
    resetWrites();
    const { user } = renderApp(<TasksSection />, { route: "/tasks", withMeta: false });
    const list = await screen.findByRole("list", { name: "tasks" });
    const blocked = within((await within(list).findAllByRole("listitem"))[3]);
    await user.click(blocked.getByRole("button", { name: /^Reprocess/ }));
    expect(blocked.getByText(/not on the allowed list/)).toBeInTheDocument();
    expect(writes.some((w) => w.path === "/api/trigger-reprocess")).toBe(false);
    await user.click(blocked.getByRole("button", { name: "Run it" }));
    await waitFor(() =>
      expect(writes.filter((w) => w.path === "/api/trigger-reprocess").map((w) => w.body)).toEqual([
        { request_id: "t-blocked" },
      ]),
    );
  });

  it("shows a running task's progress on its card", async () => {
    renderApp(<TasksSection />, { route: "/tasks", withMeta: false });
    expect(await screen.findByText(/3 tool calls · 1\/2 subagents running · 1 action \(last: reply\)/)).toBeInTheDocument();
    expect(screen.getByText("Bash")).toBeInTheDocument();
  });

  it("shows when each task came in, and a failed run's estimated cost, while folded", async () => {
    renderApp(<TasksSection />, { route: "/tasks", withMeta: false });
    const list = await screen.findByRole("list", { name: "tasks" });
    const failed = (await within(list).findAllByRole("listitem"))[2];
    expect(failed.querySelector("time")).toHaveAttribute("dateTime", "2026-09-24T10:00:00+00:00");
    expect(failed.querySelector("time")?.textContent).toMatch(/Sep 24/);
    expect(within(failed).getByText("~$1.23")).toHaveAttribute("title", expect.stringMatching(/estimated/));
  });

  it("searches the message, the name and who asked", async () => {
    const { user } = renderApp(<TasksSection />, { route: "/tasks", withMeta: false });
    const list = await screen.findByRole("list", { name: "tasks" });
    await user.type(screen.getByLabelText("search tasks"), "tomas");
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByText("Duty Cards: report the route-group error")).toBeInTheDocument();
  });

  it("filters by what is switched off, so a new value still shows", () => {
    expect(filterTasks(tasksFixture.tasks, "", { source: ["slack"] }).map((t) => t.id)).toEqual([
      "t-done",
      "t-failed",
      "t-blocked",
    ]);
    expect(filterTasks(tasksFixture.tasks, "", { requester: ["Alex", "Stranger"], status: ["done"] }).map((t) => t.id)).toEqual([
      "t-running",
    ]);
    expect(filterTasks(tasksFixture.tasks, "triage abc-1", {}).map((t) => t.id)).toEqual(["t-failed"]);
  });

  it("opens a task to its trigger, command, output, actions and tool calls", async () => {
    let asked = 0;
    server.use(
      http.get("/api/task", async () => {
        asked += 1;
        const { taskDetailFixture } = await import("@/test/fixtures");
        return HttpResponse.json(taskDetailFixture);
      }),
    );
    const { user } = renderApp(<TasksSection />, { route: "/tasks", withMeta: false });
    const list = await screen.findByRole("list", { name: "tasks" });
    expect(asked).toBe(0);
    await user.click(within(list).getByRole("button", { name: /from Maria, running/ }));
    await waitFor(() => expect(asked).toBe(1));

    expect(await screen.findByText(/claude -p <prompt> --output-format json --session-id s-1/)).toBeInTheDocument();
    expect(screen.getByText("Reading the thread now.")).toBeInTheDocument();
    expect(screen.getAllByText("https://slack.example/r").length).toBeGreaterThan(0);

    // What the hooks wrote to the database: subagents and a live timeline.
    const subs = screen.getByRole("list", { name: "subagents" });
    expect(within(subs).getByText("pr-reviewer")).toBeInTheDocument();
    const activity = screen.getByRole("list", { name: "activity" });
    expect(within(activity).getByText("subagent pr-reviewer started")).toBeInTheDocument();
    expect(within(activity).getByText("reply")).toBeInTheDocument();

    const session = screen.getByRole("list", { name: "session" });
    await user.click(within(session).getByRole("button", { name: /mcp__slack__read_thread/ }));
    expect(within(session).getByText("THREAD: release is friday")).toBeInTheDocument();
  });

  it("measures a duration in seconds, minutes and hours", () => {
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T00:00:42Z")).toBe("42s");
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T00:04:12Z")).toBe("4m 12s");
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T01:05:00Z")).toBe("1h 5m");
  });
});
