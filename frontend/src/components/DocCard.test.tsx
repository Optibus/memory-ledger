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
});
