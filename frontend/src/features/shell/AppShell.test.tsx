import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { App } from "@/App";
import { renderApp } from "@/test/utils";
import { failWith, server } from "@/test/server";
import { metaFixture } from "@/test/fixtures";

describe("the app shell", () => {
  it("lands on the map", async () => {
    renderApp(<App />, { route: "/", withMeta: false });
    expect(await screen.findByText(/nodes · 3 edges/)).toBeInTheDocument();
  });

  it("opens a section straight from its URL", async () => {
    renderApp(<App />, { route: "/dashboard", withMeta: false });
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("sends a stale link back to the map rather than to a 404 page", async () => {
    renderApp(<App />, { route: "/no-such-section", withMeta: false });
    expect(await screen.findByText(/nodes · 3 edges/)).toBeInTheDocument();
  });

  it("navigates between sections through the drawer", async () => {
    const { user } = renderApp(<App />, { route: "/map", withMeta: false });
    await screen.findByText(/nodes · 3 edges/);
    await user.click(screen.getByRole("button", { name: "sections" }));
    await user.click(screen.getByRole("link", { name: /Skills/ }));
    expect(await screen.findByRole("heading", { name: "Skills" })).toBeInTheDocument();
  });

  it("toggles the drawer with the m key, and not while typing", async () => {
    const { user } = renderApp(<App />, { route: "/map", withMeta: false });
    await screen.findByText(/nodes · 3 edges/);
    const burger = screen.getByRole("button", { name: "sections" });
    expect(burger).toHaveAttribute("aria-expanded", "false");

    await user.keyboard("m");
    expect(burger).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("m");
    expect(burger).toHaveAttribute("aria-expanded", "false");

    await user.type(screen.getByRole("searchbox"), "m");
    expect(burger).toHaveAttribute("aria-expanded", "false");
  });

  it("tells the document which section is open, so the map can own the viewport", async () => {
    renderApp(<App />, { route: "/settings", withMeta: false });
    await screen.findByRole("heading", { name: "Settings" });
    await waitFor(() => expect(document.body.dataset.section).toBe("settings"));
  });

  it("says the vocabulary is unreadable rather than silently going read-only", async () => {
    failWith("get", "/api/meta", 500, "meta blew up");
    renderApp(<App />, { route: "/map", withMeta: false });
    expect(await screen.findByText(/Could not read the server vocabulary/)).toBeInTheDocument();
  });

  it("puts the create button only where contracts are authored", async () => {
    const { user } = renderApp(<App />, { route: "/dashboard", withMeta: false });
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.queryByRole("button", { name: /create a skill or a rule/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "sections" }));
    await user.click(screen.getByRole("link", { name: /Skills/ }));
    expect(await screen.findByRole("button", { name: /create a skill or a rule/ })).toBeInTheDocument();
  });

  it("hides the create button on a read-only server", async () => {
    server.use(http.get("/api/meta", () => HttpResponse.json({ ...metaFixture, writable: false })));
    renderApp(<App />, { route: "/skills", withMeta: false });
    await screen.findByRole("heading", { name: "Skills" });
    expect(screen.queryByRole("button", { name: /create a skill or a rule/ })).not.toBeInTheDocument();
  });
});
