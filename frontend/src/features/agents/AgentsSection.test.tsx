import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { AgentsSection } from "./AgentsSection";
import { renderApp } from "@/test/utils";
import { resetWrites, writes } from "@/test/server";
import { agentThreadFixture } from "@/test/fixtures";
import { toText, formatCost } from "./export";

beforeEach(resetWrites);

const sent = (path: string) => writes.filter((w) => w.path === path).map((w) => w.body);

describe("Agents", () => {
  it("lists active conversations with what each reply is doing, and folds hidden ones away", async () => {
    const { user } = renderApp(<AgentsSection />, { route: "/agents" });
    const list = await screen.findByRole("list", { name: "active conversations" });
    const rows = within(list).getAllByRole("button");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]!).getByRole("img", { name: "the agent asked you something" })).toBeInTheDocument();
    expect(within(rows[1]!).getByRole("img", { name: "waiting for the agent" })).toBeInTheDocument();
    expect(within(rows[2]!).getByRole("img", { name: "answered" })).toBeInTheDocument();
    expect(screen.queryByText("Old idea")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Hidden \(1\)/ }));
    expect(within(screen.getByRole("list", { name: "hidden conversations" })).getByText("Old idea")).toBeInTheDocument();
  });

  it("puts yours on the right and the agent's on the left, its tool log folded shut", async () => {
    renderApp(<AgentsSection />, { route: "/agents?c=c1" });
    const thread = await screen.findByRole("list", { name: "agent messages" });
    const mine = await within(thread).findByRole("listitem", { name: "your message" });
    const reply = within(thread).getByRole("listitem", { name: "agent reply" });
    expect(mine.className).toContain("--out");
    expect(reply.className).toContain("--in");
    const log = reply.querySelector("details.ml-agents__log") as HTMLDetailsElement;
    expect(log.open).toBe(false);
    expect(log.querySelector("summary")?.textContent).toMatch(/1 tool .*1 skill/);
    expect(within(reply).getByRole("img", { name: "the agent asked you something" })).toBeInTheDocument();
  });

  it("shows the conversation's cost at the top right", async () => {
    renderApp(<AgentsSection />, { route: "/agents?c=c1" });
    expect(await screen.findByLabelText("conversation cost")).toHaveTextContent("$0.01");
  });

  it("searches inside the chat", async () => {
    const { user } = renderApp(<AgentsSection />, { route: "/agents?c=c1" });
    await screen.findByRole("listitem", { name: "agent reply" });
    await user.click(screen.getByRole("button", { name: "search in chat" }));
    await user.type(screen.getByLabelText("search in this chat"), "renew");
    expect(await screen.findByText("1 of 2 messages match")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "agent reply" })).not.toBeInTheDocument();
  });

  it("hides a conversation", async () => {
    const { user } = renderApp(<AgentsSection />, { route: "/agents?c=c1" });
    await screen.findByRole("listitem", { name: "agent reply" });
    await user.click(screen.getByRole("button", { name: "Hide" }));
    await waitFor(() => expect(sent("/api/agent-conversation")).toEqual([{ id: "c1", hidden: true }]));
  });

  it("switches the model mid-conversation", async () => {
    const { user } = renderApp(<AgentsSection />, { route: "/agents?c=c1" });
    await screen.findByRole("listitem", { name: "agent reply" });
    await user.selectOptions(screen.getByLabelText("agent"), "ollama");
    await waitFor(() =>
      expect(sent("/api/agent-conversation")).toEqual([{ id: "c1", connector: "ollama", model: "llama3:latest" }]),
    );
  });

  it("starts a conversation read-only with the default agent", async () => {
    const { user } = renderApp(<AgentsSection />, { route: "/agents" });
    await screen.findByText("Start a conversation");
    await waitFor(() => expect(screen.getByLabelText("model")).toHaveValue("claude-sonnet-5-5"));
    await user.type(screen.getByLabelText("message to the agent"), "Hello{Enter}");
    await waitFor(() =>
      expect(sent("/api/agent-send")).toEqual([
        { text: "Hello", connector: "claude", model: "claude-sonnet-5-5", full_tools: false, context_turns: 1 },
      ]),
    );
  });
});

describe("agent export", () => {
  it("writes a conversation as plain text, tool calls listed", () => {
    const txt = toText(agentThreadFixture.conversation, agentThreadFixture.messages);
    expect(txt).toContain("# Parking permit");
    expect(txt).toContain("You:");
    expect(txt).toContain("· tool Grep");
    expect(txt).toContain("claude-sonnet-5-5 (claude) [question]:");
  });

  it("prices a local model as free and an estimate as approximate", () => {
    expect(formatCost(0, false, true)).toBe("$0 · local");
    expect(formatCost(0.0123, true)).toBe("≈ $0.01");
    expect(formatCost(0.0042)).toBe("$0.0042");
  });
});
