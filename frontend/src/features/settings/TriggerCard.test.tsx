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
          not_allowed: "You can't ask me that here, so I did nothing.",
        },
      }),
    );
  });

  it("refuses without a model call by default, and only then may translation be switched on", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const noModel = await screen.findByRole("checkbox", { name: "Refuse without a model call" });
    expect(noModel).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Reply in the sender's language" })).toBeDisabled();
    await user.click(noModel);
    await waitFor(() => expect(sent()).toContainEqual({ refuse_without_model: false }));
  });

  it("switches translation on its own once refusals may use a model", async () => {
    server.use(
      http.get("/api/trigger", () =>
        HttpResponse.json({ ...triggerFixture, config: { ...triggerFixture.config, refuse_without_model: false } }),
      ),
    );
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    await user.click(await screen.findByRole("checkbox", { name: "Reply in the sender's language" }));
    await waitFor(() => expect(sent()).toContainEqual({ translate_notices: false }));
    const slack = await panel("Slack");
    expect(slack.getByText(/through your Slack MCP instead/)).toBeInTheDocument();
  });

  it("says a refusal with no user token is only logged, when refusals make no model call", async () => {
    renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    expect(slack.getByText(/Without it a refusal is only logged/)).toBeInTheDocument();
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

  it("lists the Jira people who may tag the brain, each with its own switch", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const jira = await panel("Jira");
    const toggle = jira.getByRole("button", { name: /People who may tag the brain/ });
    expect(within(toggle).getByText("1")).toBeInTheDocument();
    await user.click(toggle);
    const list = jira.getByRole("list", { name: "allowed people" });
    const [isadora, former] = within(list).getAllByRole("checkbox", { name: "May tag the brain" });
    expect(isadora).toBeChecked();
    expect(former).not.toBeChecked();
    expect(within(list).getByText(/off since 2026-09-10 by web-ui/)).toBeInTheDocument();
    await user.click(isadora);
    await waitFor(() =>
      expect(sent("/api/trigger-person")).toContainEqual({
        source: "jira", user_id: "62f3dd0ce50f2f2a39573991", enabled: false, name: "Isadora",
      }),
    );
    await user.click(former);
    await waitFor(() =>
      expect(sent("/api/trigger-person")).toContainEqual({
        source: "jira", user_id: "557058:aaaa-bbbb", enabled: true, name: "Former",
      }),
    );
  });

  it("finds a Jira person by name and allows them", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const jira = await panel("Jira");
    await user.click(jira.getByRole("button", { name: /People who may tag the brain/ }));
    await user.type(jira.getByLabelText("Find a person"), "isa");
    expect(jira.queryByRole("button", { name: "Allow this account id" })).toBeNull();
    await user.click(jira.getByRole("button", { name: "Search Jira" }));
    const found = await jira.findByRole("list", { name: "people found" });
    expect(within(found).getByRole("button", { name: "Allow Isadora" })).toBeDisabled();
    await user.click(within(found).getByRole("button", { name: "Allow Isaac" }));
    await waitFor(() =>
      expect(sent("/api/trigger-person")).toContainEqual({
        source: "jira", user_id: "557058:cccc-dddd", enabled: true, name: "Isaac",
      }),
    );
  });

  it("allows a pasted account id without a search", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const jira = await panel("Jira");
    await user.click(jira.getByRole("button", { name: /People who may tag the brain/ }));
    await user.type(jira.getByLabelText("Find a person"), "712020:0f1e2d3c-4b5a");
    await user.click(jira.getByRole("button", { name: "Allow this account id" }));
    await waitFor(() =>
      expect(sent("/api/trigger-person")).toContainEqual({
        source: "jira", user_id: "712020:0f1e2d3c-4b5a", enabled: true,
      }),
    );
  });

  it("lists the newest requests", async () => {
    renderApp(<TriggerCard writable />, { route: "/settings" });
    const list = await screen.findByRole("list", { name: "recent requests" });
    expect(within(list).getByText(/Maria: can you summarise this thread/)).toBeInTheDocument();
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
    expect(ev.ci_failed).toMatchObject({ on: true, do: "Fix it and push.", action: "claude" });
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
        events: { mention: { do: "{trigger_source} sent this task. Reply to {trigger_user}." } },
      }),
    );
  });

  it("folds a panel away, and remembers it", async () => {
    const { user, unmount } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const jira = await panel("Jira");
    const toggle = jira.getByRole("button", { name: "Jira" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);
    expect(jira.queryByLabelText("Poll every (seconds)")).not.toBeInTheDocument();
    expect(jira.getByText("switched off")).toBeInTheDocument();
    unmount();
    renderApp(<TriggerCard writable />, { route: "/settings" });
    const again = await panel("Jira");
    expect(again.getByRole("button", { name: "Jira" })).toHaveAttribute("aria-expanded", "false");
    await user.click(again.getByRole("button", { name: "Jira" }));
    expect(again.getByLabelText("Poll every (seconds)")).toBeInTheDocument();
  });

  it("saves who may and may not summon the brain on Slack", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    await user.type(slack.getByLabelText("Blocked people"), "u0pest, U0NOISE");
    await user.click(slack.getByRole("button", { name: "Save Slack" }));
    await waitFor(() =>
      expect(sent()[0].slack).toMatchObject({ allowed_users: ["U0FRIEND"], blocked_users: ["U0PEST", "U0NOISE"] }),
    );
  });

  it("listens in your DMs only when asked, and opens them all only after that", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const slack = await panel("Slack");
    const open = slack.getByRole("checkbox", { name: "Anyone who messages you directly may summon it" });
    expect(open).toBeDisabled();
    await user.click(slack.getByRole("checkbox", { name: "Listen in your direct messages" }));
    expect(open).toBeEnabled();
    await user.click(slack.getByRole("button", { name: "Save Slack" }));
    await waitFor(() => expect(sent()[0].slack).toMatchObject({ dms: true, dms_open: false }));
  });

  it("switches on a Slack event and has it call an API instead of a session", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const events = within(await screen.findByRole("group", { name: "Slack events" }));
    await user.click(events.getByRole("checkbox", { name: "The brain is added to a channel" }));
    const actions = events.getByRole("group", { name: "Action for The brain is added to a channel" });
    await user.click(within(actions).getByRole("button", { name: "Call an API" }));
    await user.type(events.getByLabelText("URL"), "https://hooks.example/joined");
    await user.click((await panel("Slack")).getByRole("button", { name: "Save Slack" }));
    await waitFor(() => expect(sent()).toHaveLength(1));
    const ev = (sent()[0].slack as { events: Record<string, Record<string, unknown>> }).events;
    expect(ev.joined_channel).toMatchObject({
      on: true, action: "http", http: { method: "POST", url: "https://hooks.example/joined" },
    });
  });

  it("lists the Slack commands folded, and saves a new one that runs a new script", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const cmds = within(await screen.findByRole("group", { name: "Slack commands" }));
    expect(cmds.getByText("deploy")).toBeInTheDocument();
    expect(cmds.getByText("runs dev_env.py, only you")).toBeInTheDocument();
    await user.click(cmds.getByRole("button", { name: "Add a command" }));
    const row = within(cmds.getByRole("group", { name: "Command new command" }));
    await user.type(row.getByLabelText("Name"), "rota");
    await user.type(row.getByLabelText("Pattern"), "^rota (?P<team>\\w+)");
    expect(cmds.getByText("{team}")).toBeInTheDocument();
    const again = within(cmds.getByRole("group", { name: "Command rota" }));
    await user.click(within(again.getByRole("group", { name: "Action for command rota" })).getByRole("button", { name: "Run a script" }));
    await user.click(again.getByRole("button", { name: "New script…" }));
    const form = within(again.getByRole("group", { name: "New script" }));
    await user.type(form.getByLabelText("File name"), "new.sh");
    expect((form.getByLabelText("Script text") as HTMLTextAreaElement).value).toContain("BRAIN_TRIGGER_USER");
    await user.click(form.getByRole("button", { name: "Create and link" }));
    await waitFor(() =>
      expect(sent("/api/create-script")).toContainEqual(expect.objectContaining({ name: "new.sh", executable: true })),
    );
    await user.click((await panel("Slack")).getByRole("button", { name: "Save Slack" }));
    await waitFor(() => expect(sent()).toHaveLength(1));
    const commands = (sent()[0].slack as { commands: Record<string, unknown>[] }).commands;
    expect(commands).toHaveLength(2);
    expect(commands[1]).toMatchObject({
      name: "rota", pattern: "^rota (?P<team>\\w+)", action: "script", script: { name: "new.sh" }, anyone: false,
    });
  });

  it("switches on Jira transitions with their statuses, and saves the watch JQL", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const events = within(await screen.findByRole("group", { name: "Jira events" }));
    await user.click(events.getByRole("checkbox", { name: "A watched issue changes status" }));
    expect(events.getByText(/Note In Progress to In Review\./)).toBeInTheDocument();
    const jira = await panel("Jira");
    const watch = jira.getByLabelText("Watch JQL");
    await user.clear(watch);
    await user.type(watch, "project = OPS");
    await user.click(jira.getByRole("button", { name: "Save Jira" }));
    await waitFor(() =>
      expect(sent()[0].jira).toMatchObject({
        watch_jql: "project = OPS", events: { transitioned: { on: true, action: "claude" } },
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

  it("lists the wake phrases as they are said, with the brain's name", async () => {
    renderApp(<TriggerCard writable />, { route: "/settings" });
    const meetings = await panel("Meetings");
    const phrases = within(meetings.getByRole("group", { name: "Wake phrases" }));
    expect(phrases.getByText("Jarvis will handle")).toBeInTheDocument();
    expect(phrases.getByText(/only you/)).toBeInTheDocument();
  });

  it("adds a phrase and saves the whole list", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const meetings = await panel("Meetings");
    await user.click(meetings.getByRole("button", { name: "Add a phrase" }));
    const field = meetings.getAllByLabelText("Phrase").at(-1)!;
    await user.clear(field);
    await user.type(field, "{{brain} take note");
    await user.click(meetings.getByRole("checkbox", { name: "Anyone in the meeting may say it" }));
    await user.click(meetings.getByRole("button", { name: "Save Meetings" }));
    await waitFor(() => {
      const body = sent().find((b) => b.meeting) as { meeting: { phrases: { phrase: string; anyone: boolean }[] } };
      expect(body.meeting.phrases.map((p) => [p.phrase, p.anyone])).toEqual([
        ["{brain} will handle", false],
        ["{brain} take note", true],
      ]);
    });
  });

  it("tries a transcript without starting anything, then runs it", async () => {
    const { user } = renderApp(<TriggerCard writable />, { route: "/settings" });
    const meetings = await panel("Meetings");
    await user.click(meetings.getByRole("button", { name: /Scan a transcript/ }));
    await user.type(meetings.getByLabelText("Transcript"), "Alex: Jarvis will handle this");
    await user.click(meetings.getByRole("button", { name: "Try" }));
    expect(await meetings.findByText("would start")).toBeInTheDocument();
    expect(meetings.getByText("would be refused")).toBeInTheDocument();
    await user.click(meetings.getByRole("button", { name: "Run" }));
    expect(await meetings.findByText("started")).toBeInTheDocument();
    expect(sent("/api/trigger-scan").map((b) => b.dry_run)).toEqual([true, false]);
  });
});
