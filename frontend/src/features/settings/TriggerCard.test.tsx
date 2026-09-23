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
    expect(body).toMatchObject({ owner_user_id: "U0OWNER" });
    expect(body).not.toHaveProperty("channels");
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

  it("saves both refusal notices, and switches translation on its own", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const policy = await screen.findByLabelText("Refusal: against a rule");
    await user.clear(policy);
    await user.type(policy, "No: {{reason}");
    await user.click(screen.getByRole("button", { name: "Save replies" }));
    await waitFor(() =>
      expect(sent()[0]).toMatchObject({
        refusals: {
          not_enabled: "I'm not enabled in this conversation, so I did nothing.",
          policy: "No: {reason}",
        },
      }),
    );
    await user.click(screen.getByRole("checkbox", { name: "Reply in the sender's language" }));
    await waitFor(() => expect(sent()).toContainEqual({ translate_notices: false }));
  });

  it("says a missing user token is fine: notices go through the MCP", async () => {
    renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    expect(slack.getByText(/through your Slack MCP instead/)).toBeInTheDocument();
  });

  it("keeps the channel list folded until it is clicked open", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    const toggle = slack.getByRole("button", { name: /Open channels/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(toggle).getByText("2")).toBeInTheDocument();
    expect(slack.queryByRole("list", { name: "open channels" })).not.toBeInTheDocument();
    await user.click(toggle);
    const list = slack.getByRole("list", { name: "open channels" });
    expect(within(list).getByText("#general")).toBeInTheDocument();
    expect(within(list).getByText("#ops")).toBeInTheDocument();
  });

  it("opens a channel from what is pasted, and closes one", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    await user.click(slack.getByRole("button", { name: /Open channels/ }));
    await user.type(slack.getByLabelText("Channel"), "https://x.slack.com/archives/C0NEW/p1");
    await user.click(slack.getByRole("button", { name: "Open channel" }));
    await waitFor(() =>
      expect(sent("/api/trigger-channel")).toContainEqual({
        channel: "https://x.slack.com/archives/C0NEW/p1", name: "", enabled: true,
      }),
    );
    await user.click(slack.getByRole("button", { name: "Close #ops" }));
    await waitFor(() =>
      expect(sent("/api/trigger-channel")).toContainEqual({ channel: "C0OPS", name: "ops", enabled: false }),
    );
  });

  it("keeps closed channels, and can reopen one", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    await user.click(slack.getByRole("button", { name: /Open channels/ }));
    expect(slack.queryByRole("list", { name: "closed channels" })).not.toBeInTheDocument();
    await user.click(slack.getByRole("button", { name: /Closed/ }));
    const closed = slack.getByRole("list", { name: "closed channels" });
    expect(within(closed).getByText("#old-project")).toBeInTheDocument();
    expect(within(closed).getByText(/closed 2026-09-10 by web-ui/)).toBeInTheDocument();
    await user.click(within(closed).getByRole("button", { name: "Reopen #old-project" }));
    await waitFor(() =>
      expect(sent("/api/trigger-channel")).toContainEqual({ channel: "C0OLD", name: "old-project", enabled: true }),
    );
  });

  it("will not open an empty channel", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    await user.click(slack.getByRole("button", { name: /Open channels/ }));
    expect(slack.getByRole("button", { name: "Open channel" })).toBeDisabled();
  });

  it("lists the newest requests", async () => {
    renderApp(<TriggerCard writable />, { route: "/settings" });
    const list = await screen.findByRole("list", { name: "recent requests" });
    expect(within(list).getByText(/Isadora: can you summarise this thread/)).toBeInTheDocument();
  });

  it("has a GitHub panel with its own switch and a write-only token", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const gh = await panel("GitHub");
    await user.click(gh.getByRole("checkbox", { name: "Poll GitHub" }));
    await waitFor(() => expect(sent()).toContainEqual({ github: { enabled: true } }));
    await user.type(gh.getByLabelText(/^Token/), "github_pat_new");
    await user.click(gh.getByRole("button", { name: "Save GitHub" }));
    await waitFor(() => expect(sent()).toHaveLength(2));
    const body = sent()[1].github as Record<string, unknown>;
    expect(body.token).toBe("github_pat_new");
    expect(body.repos).toEqual(["acme/api"]);
  });

  it("picks which GitHub changes start the flow, each with its instruction", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const events = within(await screen.findByRole("group", { name: "GitHub events" }));
    expect(events.queryAllByLabelText("Run command")).toHaveLength(3);
    await user.click(events.getByRole("checkbox", { name: "Checks fail on your pull request" }));
    await user.click(events.getByRole("checkbox", { name: "Your pull request is reviewed" }));
    const boxes = events.getAllByLabelText("Run command");
    expect(boxes).toHaveLength(3);
    const ci = boxes[boxes.length - 1];
    expect(ci).toHaveValue("Find the likely cause.");
    await user.clear(ci);
    await user.type(ci, "Fix it and push.");
    await user.click((await panel("GitHub")).getByRole("button", { name: "Save GitHub" }));
    await waitFor(() => expect(sent()).toHaveLength(1));
    const ev = (sent()[0].github as { events: Record<string, { on: boolean; do: string }> }).events;
    expect(ev.ci_failed).toEqual({ on: true, do: "Fix it and push." });
    expect(ev.review.on).toBe(false);
    expect(ev.mention.on).toBe(true);
  });

  it("documents the run command variables once, at the top", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    await user.click(await screen.findByText("Run commands and their variables"));
    const table = screen.getByRole("table");
    expect(within(table).getByText("{trigger_user}")).toBeInTheDocument();
    const repo = within(table).getByText("{github_repo}").closest("tr") as HTMLElement;
    expect(within(repo).getByText("GitHub")).toBeInTheDocument();
    const src = within(table).getByText("{trigger_source}").closest("tr") as HTMLElement;
    expect(within(src).getByText("every trigger")).toBeInTheDocument();
  });

  it("saves Slack's run command, previewing it with example values", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    const box = slack.getByLabelText("Run command");
    await user.click(box);
    await user.paste("{trigger_source} sent this task. Reply to {trigger_user}.");
    expect(slack.getByText(/github sent this task\. Reply to octocat\./)).toBeInTheDocument();
    await user.click(slack.getByRole("button", { name: "Save Slack" }));
    await waitFor(() =>
      expect(sent()[0].slack).toMatchObject({
        prompt: "{trigger_source} sent this task. Reply to {trigger_user}.",
      }),
    );
  });

  it("flags a variable that does not exist", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const events = within(await screen.findByRole("group", { name: "GitHub events" }));
    const box = events.getAllByLabelText("Run command")[1];
    await user.clear(box);
    await user.click(box);
    await user.paste("Review {github_repo} for {trigger_usr}");
    expect(events.getByText(/Not a variable: \{trigger_usr\}/)).toBeInTheDocument();
  });
});
