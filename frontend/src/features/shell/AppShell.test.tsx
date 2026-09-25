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
    await user.click(screen.getByRole("link", { name: /Capabilities/ }));
    expect(await screen.findByRole("heading", { name: "Capabilities" })).toBeInTheDocument();
  });

  it("reaches the People section, which the drawer now carries", async () => {
    const { user } = renderApp(<App />, { route: "/map", withMeta: false });
    await screen.findByText(/nodes · 3 edges/);
    await user.click(screen.getByRole("button", { name: "sections" }));
    await user.click(screen.getByRole("link", { name: /People/ }));
    expect(await screen.findByRole("heading", { name: "People" })).toBeInTheDocument();
  });

  it("reaches the Usage report through the drawer, and it owns the viewport", async () => {
    const { user } = renderApp(<App />, { route: "/map", withMeta: false });
    await screen.findByText(/nodes · 3 edges/);
    await user.click(screen.getByRole("button", { name: "sections" }));
    await user.click(screen.getByRole("link", { name: /Usage/ }));
    expect(await screen.findByRole("heading", { name: "Usage" })).toBeInTheDocument();
    expect(await screen.findByTitle("usage report")).toBeInTheDocument();
    await waitFor(() => expect(document.body.dataset.section).toBe("usage"));
  });

  it("opens the server's API docs inside the app, framed", async () => {
    const { user } = renderApp(<App />, { route: "/map", withMeta: false });
    await screen.findByText(/nodes · 3 edges/);
    await user.click(screen.getByRole("button", { name: "sections" }));
    await user.click(screen.getByRole("link", { name: "API docs" }));

    expect(await screen.findByRole("heading", { name: "API docs" })).toBeInTheDocument();
    // Swagger is rendered by the server, so the page frames it rather than
    // reimplementing it: the drawer and the URL stay in the app.
    expect(screen.getByTitle("API docs")).toHaveAttribute("src", "./api/docs");
    await waitFor(() => expect(document.body.dataset.section).toBe("api-docs"));
  });

  it("keeps an arrow beside it that opens the docs in a new tab", async () => {
    const { user } = renderApp(<App />, { route: "/map", withMeta: false });
    await screen.findByText(/nodes · 3 edges/);
    await user.click(screen.getByRole("button", { name: "sections" }));

    const arrow = screen.getByRole("link", { name: "open the API docs in a new tab" });
    // A real navigation, not a route: the new tab is the server's own page.
    expect(arrow).toHaveAttribute("href", "./api/docs");
    expect(arrow).toHaveAttribute("target", "_blank");
    expect(arrow).toHaveAttribute("rel", "noreferrer");
  });

  it("opens the API docs page straight from its URL", async () => {
    renderApp(<App />, { route: "/api-docs", withMeta: false });
    expect(await screen.findByRole("heading", { name: "API docs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open in a new tab/ })).toHaveAttribute(
      "target",
      "_blank",
    );
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
    expect(screen.queryByRole("button", { name: /create a process, a skill or a rule/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "sections" }));
    await user.click(screen.getByRole("link", { name: /Capabilities/ }));
    expect(await screen.findByRole("button", { name: /create a process, a skill or a rule/ })).toBeInTheDocument();
  });

  it("hides the create button on a read-only server", async () => {
    server.use(http.get("/api/meta", () => HttpResponse.json({ ...metaFixture, writable: false })));
    // the old /skills link still lands on the tab it became
    renderApp(<App />, { route: "/skills", withMeta: false });
    await screen.findByRole("heading", { name: "Capabilities" });
    expect(screen.queryByRole("button", { name: /create a process, a skill or a rule/ })).not.toBeInTheDocument();
  });
});
