import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { MessagesSection } from "./MessagesSection";
import { renderApp } from "@/test/utils";
import { http, HttpResponse } from "msw";
import { resetWrites, server, writes } from "@/test/server";
import { b2bThreadFixture } from "@/test/fixtures";

beforeEach(resetWrites);

const DANA = "A".repeat(43);
const sent = (path: string) => writes.filter((w) => w.path === path).map((w) => w.body);

describe("Messages", () => {
  it("lists the brains talked to, newest first, with who wrote the last message", async () => {
    renderApp(<MessagesSection />, { route: "/messages" });
    const list = await screen.findByRole("list", { name: "conversation list" });
    const rows = within(list).getAllByRole("button");
    expect(rows.map((r) => within(r).getByText(/Dana|Spammer/).textContent)).toEqual(["Dana", "Spammer"]);
    // a connected peer with no messages yet is not a conversation
    expect(within(list).queryByText("Eli")).not.toBeInTheDocument();
    expect(within(rows[0]).getByRole("img", { name: "written by a person" })).toBeInTheDocument();
    expect(within(rows[1]).getByRole("img", { name: "automated" })).toBeInTheDocument();
  });

  it("searches conversations by name or text", async () => {
    const { user } = renderApp(<MessagesSection />, { route: "/messages" });
    const list = await screen.findByRole("list", { name: "conversation list" });
    await user.type(screen.getByLabelText("search conversations"), "buy");
    expect(within(list).getByText("Spammer")).toBeInTheDocument();
    expect(within(list).queryByText("Dana")).not.toBeInTheDocument();
  });

  it("shows a thread with every message flagged and stamped", async () => {
    renderApp(<MessagesSection />, { route: `/messages?peer=${DANA}` });
    const thread = await screen.findByRole("list", { name: "messages" });
    const items = await within(thread).findAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("Can you review my PR?")).toBeInTheDocument();
    expect(within(items[0]).getByRole("img", { name: "automated" })).toBeInTheDocument();
    expect(within(items[1]).getByRole("img", { name: "written by a person" })).toBeInTheDocument();
    expect(items[1].querySelector("time")?.getAttribute("dateTime")).toBe("2026-09-29T11:00:00Z");
  });

  it("sends what is typed here as a person's message", async () => {
    const { user } = renderApp(<MessagesSection />, { route: `/messages?peer=${DANA}` });
    await screen.findByRole("list", { name: "messages" });
    await user.type(screen.getByLabelText("message"), "Thanks!{Enter}");
    await waitFor(() =>
      expect(sent("/api/b2b-send")).toEqual([{ to: DANA, text: "Thanks!", human: true }]),
    );
  });

  it("starts a new conversation with a connected peer", async () => {
    const { user } = renderApp(<MessagesSection />, { route: "/messages" });
    await screen.findByRole("list", { name: "conversation list" });
    await user.click(screen.getByRole("button", { name: /New/ }));
    const peers = screen.getByRole("list", { name: "peers" });
    // only connected peers can be written to
    expect(within(peers).queryByText("Spammer")).not.toBeInTheDocument();
    await user.click(within(peers).getByText("Eli"));
    expect(await screen.findByText("No messages yet")).toBeInTheDocument();
    expect(screen.getByLabelText("message")).toBeEnabled();
  });

  it("will not write to a brain that is not a connected peer", async () => {
    renderApp(<MessagesSection />, { route: `/messages?peer=${"D".repeat(43)}` });
    expect(await screen.findByText(/Not a connected peer/)).toBeInTheDocument();
    expect(screen.getByLabelText("message")).toBeDisabled();
  });

  it("shows a message's formatting and links, and never its markup", async () => {
    server.use(
      http.get("/api/b2b-thread", () =>
        HttpResponse.json({
          ...b2bThreadFixture,
          messages: [
            {
              ...b2bThreadFixture.messages[0]!,
              text: "**Done**, see [the PR](https://g.example/pr/1) or https://x.example. __really__ <b>hi</b>",
            },
          ],
        }),
      ),
    );
    renderApp(<MessagesSection />, { route: `/messages?peer=${DANA}` });
    const thread = await screen.findByRole("list", { name: "messages" });
    expect((await within(thread).findByText("Done")).tagName).toBe("STRONG");
    expect(within(thread).getByText("really").tagName).toBe("U");
    const pr = within(thread).getByRole("link", { name: "the PR" });
    expect(pr).toHaveAttribute("href", "https://g.example/pr/1");
    expect(pr).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(thread).getByRole("link", { name: "https://x.example" })).toBeInTheDocument();
    // HTML in a message is text, not markup.
    expect(thread.querySelector("b")).toBeNull();
    expect(within(thread).getByText(/<b>hi<\/b>/)).toBeInTheDocument();
  });

  it("formats the selection from the toolbar and the keyboard", async () => {
    const { user } = renderApp(<MessagesSection />, { route: `/messages?peer=${DANA}` });
    await screen.findByRole("list", { name: "messages" });
    const box = screen.getByLabelText("message") as HTMLTextAreaElement;
    await user.type(box, "ship it");
    box.setSelectionRange(0, 4);
    await user.click(screen.getByRole("button", { name: "Bold" }));
    expect(box.value).toBe("**ship** it");
    expect(box.value.slice(box.selectionStart, box.selectionEnd)).toBe("ship");
    box.setSelectionRange(9, 11);
    await user.keyboard("{Control>}u{/Control}");
    expect(box.value).toBe("**ship** __it__");
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(sent("/api/b2b-send")).toEqual([{ to: DANA, text: "**ship** __it__", human: true }]),
    );
  });
});
