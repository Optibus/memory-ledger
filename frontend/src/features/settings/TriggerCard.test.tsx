import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { TriggerCard } from "./TriggerCard";
import { renderApp } from "@/test/utils";
import { failWith, resetWrites, server, writes } from "@/test/server";
import { triggerFixture } from "@/test/fixtures";

beforeEach(resetWrites);

const panel = async (name: string) => within(await screen.findByRole("region", { name }));
const sent = (path = "/api/trigger-config") =>
  writes.filter((w) => w.path === path).map((w) => w.body as Record<string, unknown>);

describe("Triggers", () => {
  it("shows whether it is listening, and each watcher's state", async () => {
    renderApp(<TriggerCard writable />, { route: "/settings" });
    expect(await screen.findByText("listening")).toBeInTheDocument();
    expect((await panel("Slack")).getByText("connected")).toBeInTheDocument();
    expect((await panel("Jira")).getByText("off")).toBeInTheDocument();
  });

  it("switches one service on or off at once, without touching the others", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const jira = await panel("Jira");
    await user.click(jira.getByRole("checkbox", { name: "Poll Jira" }));
    await waitFor(() => expect(sent()).toContainEqual({ jira: { enabled: true } }));
  });

  it("has a master switch above the services", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    await user.click(await screen.findByRole("checkbox", { name: "Listen for mentions" }));
    await waitFor(() => expect(sent()).toContainEqual({ enabled: false }));
  });

  it("never sends a token it was not given: blank keeps the stored one", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    await user.click(slack.getByRole("button", { name: "Save Slack" }));
    await waitFor(() => expect(sent()).toHaveLength(1));
    const body = sent()[0].slack as Record<string, unknown>;
    expect(body).toMatchObject({ owner_user_id: "U0OWNER", channels: "C1" });
    expect(body).not.toHaveProperty("app_token");
    expect(body).not.toHaveProperty("user_token");
  });

  it("sends a typed token, then clears the field", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    const field = slack.getByLabelText(/User token/);
    await user.type(field, "xoxp-new");
    await user.click(slack.getByRole("button", { name: "Save Slack" }));
    await waitFor(() =>
      expect((sent()[0].slack as Record<string, unknown>).user_token).toBe("xoxp-new"),
    );
    await waitFor(() => expect(field).toHaveValue(""));
  });

  it("removes a stored token only when asked", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    // The app token is stored; the bot token comes from the environment.
    await user.click(slack.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(sent()).toContainEqual({ clear: ["slack.app_token"] }));
  });

  it("says when the environment owns a token, and does not offer to change it", async () => {
    renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    expect(slack.getByLabelText(/Bot token/)).toBeDisabled();
    expect(slack.getByText(/Set by \$MEMORY_LEDGER_SLACK_BOT_TOKEN/)).toBeInTheDocument();
  });

  it("saves Jira's settings as numbers where they are numbers", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const jira = await panel("Jira");
    const poll = jira.getByLabelText("Poll every (seconds)");
    await user.clear(poll);
    await user.type(poll, "120");
    await user.type(jira.getByLabelText(/API token/), "atl-tok");
    await user.click(jira.getByRole("button", { name: "Save Jira" }));
    await waitFor(() =>
      expect(sent()[0].jira).toMatchObject({ poll_seconds: 120, token: "atl-tok", marker: "@brain" }),
    );
  });

  it("checks one service and lists what it found", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const jira = await panel("Jira");
    await user.click(jira.getByRole("button", { name: "Check Jira" }));
    expect(await jira.findByText("Jira is ready.")).toBeInTheDocument();
    expect(sent("/api/trigger-check")).toEqual([{ only: "jira" }]);
  });

  it("says what is wrong when a check fails", async () => {
    server.use(
      http.post("/api/trigger-check", () =>
        HttpResponse.json({
          ok: true, ready: false, target: "slack",
          problems: ["app token: slack apps.connections.open: invalid_auth"], notes: [],
        }),
      ),
    );
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    await user.click(slack.getByRole("button", { name: "Check Slack" }));
    expect(await slack.findByText("Slack is not ready.")).toBeInTheDocument();
    expect(slack.getByText(/invalid_auth/)).toBeInTheDocument();
  });

  it("shows a refused save with the server's reason", async () => {
    failWith("post", "/api/trigger-config", 400, "jira.poll_seconds must be a number from 15 to 3600");
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const jira = await panel("Jira");
    await user.click(jira.getByRole("button", { name: "Save Jira" }));
    expect(await jira.findByText(/must be a number from 15 to 3600/)).toBeInTheDocument();
  });

  it("explains a server that runs no watchers", async () => {
    server.use(
      http.get("/api/trigger", () => HttpResponse.json({ ...triggerFixture, watching: false })),
    );
    renderApp(<TriggerCard writable />, { route: "/settings" });
    expect(await screen.findByText(/runs no watchers/)).toBeInTheDocument();
  });

  it("disables every control on a read-only server", async () => {
    renderApp(<TriggerCard writable={false} />, { route: "/settings" });
    const slack = await panel("Slack");
    expect(slack.getByRole("button", { name: "Save Slack" })).toBeDisabled();
    expect(slack.getByRole("button", { name: "Check Slack" })).toBeDisabled();
    expect(slack.getByRole("checkbox", { name: "Listen on Slack" })).toBeDisabled();
  });

  it("lists the newest requests", async () => {
    renderApp(<TriggerCard writable />, { route: "/settings" });
    const list = await screen.findByRole("list", { name: "recent requests" });
    expect(within(list).getByText(/Isadora: can you summarise this thread/)).toBeInTheDocument();
  });
});
