import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { Markdown } from "./Markdown";

describe("DocCard", () => {
  it("shows only the name until expanded, then closes on the cross and on a click outside", async () => {
    server.use(
      http.get("/api/document", () =>
        HttpResponse.json({ ok: true, name: "n.md", path: "documents/n.md", kind: "md", size: 5, text: "# Hello" }),
      ),
    );
    const user = userEvent.setup();
    render(<Markdown text="[Notes](doc:documents/n.md)" />);
    expect(screen.getByText("Notes")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Expand Notes" }));
    expect(await screen.findByText("Hello")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "close" }));
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Expand Notes" }));
    const scrim = document.querySelector("[data-ml-modal]") as HTMLElement;
    await user.pointer({ keys: "[MouseLeft]", target: scrim });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows an attached image as a thumbnail that opens in the viewer", async () => {
    const user = userEvent.setup();
    render(<Markdown text="[shot.png](attach:x-shot.png)" />);
    const thumb = screen.getByRole("img", { name: "shot.png" });
    expect(thumb).toHaveAttribute("src", expect.stringContaining("/api/document-file?path=x-shot.png&area=attachments"));
    await user.click(screen.getByRole("button", { name: "Expand shot.png" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: "shot.png" })).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers a download for a file it cannot show", async () => {
    server.use(
      http.get("/api/document", () =>
        HttpResponse.json({ ok: true, name: "n.zip", path: "x-n.zip", kind: "file", size: 5, text: null }),
      ),
    );
    const user = userEvent.setup();
    render(<Markdown text="[n.zip](attach:x-n.zip)" />);
    await user.click(screen.getByRole("button", { name: "Expand n.zip" }));
    expect((await screen.findAllByRole("link", { name: "Download n.zip" })).length).toBe(2);
  });

  it("downloads straight from the card, without expanding", () => {
    render(<Markdown text="[Notes](attach:notes.md)" />);
    const link = screen.getByRole("link", { name: "Download Notes" });
    expect(link).toHaveAttribute("download", "Notes");
    expect(link.getAttribute("href")).toContain("/api/document-file");
  });
});
