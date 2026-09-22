import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { SettingsSection } from "./SettingsSection";
import { renderApp } from "@/test/utils";
import { failWith, resetWrites, server, writes } from "@/test/server";
import { settingsFixture, statusFixture } from "@/test/fixtures";

beforeEach(resetWrites);

describe("Settings", () => {
  it("shows what is stored and what is actually in effect", async () => {
    renderApp(<SettingsSection />, { route: "/settings" });
    expect(await screen.findByLabelText(/Timezone/)).toHaveValue("America/Sao_Paulo");
    expect(screen.getByText(/In effect: America\/Sao_Paulo \(from settings\)/)).toBeInTheDocument();
  });

  it("writes preferences server-side, so the CLI and the hooks agree with the page", async () => {
    const { user } = renderApp(<SettingsSection />, { route: "/settings" });
    const tz = await screen.findByLabelText(/Timezone/);
    await user.clear(tz);
    await user.type(tz, "Europe/Lisbon");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/settings")?.body).toMatchObject({
        tz: "Europe/Lisbon",
      }),
    );
  });

  it("sends an empty value rather than skipping it, because empty REMOVES a key", async () => {
    const { user } = renderApp(<SettingsSection />, { route: "/settings" });
    const tz = await screen.findByLabelText(/Timezone/);
    await user.clear(tz);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      const body = writes.find((w) => w.path === "/api/settings")?.body as Record<string, unknown>;
      expect(body).toHaveProperty("tz", "");
    });
  });

  it("warns when the shell has taken a key over, instead of pretending the save took effect", async () => {
    server.use(
      http.get("/api/settings", () =>
        HttpResponse.json({
          ...settingsFixture,
          env: { tz: "UTC" },
          effective: { ...settingsFixture.effective, tz: "UTC", tz_source: "env" },
        }),
      ),
    );
    renderApp(<SettingsSection />, { route: "/settings" });
    expect(await screen.findByText(/The shell has taken over tz/)).toBeInTheDocument();
  });

  it("reports a refused save with the server's reason", async () => {
    failWith("post", "/api/settings", 400, "unknown timezone: Mars/Olympus");
    const { user } = renderApp(<SettingsSection />, { route: "/settings" });
    await screen.findByLabelText(/Timezone/);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("unknown timezone: Mars/Olympus")).toBeInTheDocument();
  });

  it("disables saving on a read-only server", async () => {
    server.use(http.get("/api/settings", () => HttpResponse.json({ ...settingsFixture, writable: false })));
    renderApp(<SettingsSection />, { route: "/settings" });
    expect(await screen.findByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText("read-only server")).toBeInTheDocument();
  });
});

describe("Settings — where the data lives", () => {
  it("says where the brain is and how much of it there is", async () => {
    renderApp(<SettingsSection />, { route: "/settings" });
    expect(await screen.findByText("/home/brain/graph.db")).toBeInTheDocument();
    expect(screen.getByText("sqlite (local mirror)")).toBeInTheDocument();
  });

  it("says the master is optional and never shows a password", async () => {
    renderApp(<SettingsSection />, { route: "/settings" });
    expect(await screen.findByText("not configured")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Password/)).toHaveValue("");
    expect(screen.getByText("None stored yet.")).toBeInTheDocument();
  });

  it("keeps the stored password when the field is left blank", async () => {
    server.use(
      http.get("/api/status", () =>
        HttpResponse.json({
          ...statusFixture,
          neo4j: { ...statusFixture.neo4j, url: "neo4j+s://x", configured: true, up: true, password_set: true },
        }),
      ),
    );
    const { user } = renderApp(<SettingsSection />, { route: "/settings" });
    await screen.findByText("reachable");
    expect(screen.getByText("One is stored. Blank keeps it.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/neo4j-config")?.body).toMatchObject({ password: "" }),
    );
  });

  it("warns when the environment wins over the file", async () => {
    server.use(
      http.get("/api/status", () =>
        HttpResponse.json({
          ...statusFixture,
          neo4j: { ...statusFixture.neo4j, env: ["SECOND_BRAIN_NEO4J_URL"] },
        }),
      ),
    );
    renderApp(<SettingsSection />, { route: "/settings" });
    expect(await screen.findByText(/SECOND_BRAIN_NEO4J_URL/)).toBeInTheDocument();
  });

  it("says how many writes are queued on the flush button", async () => {
    server.use(http.get("/api/status", () => HttpResponse.json({ ...statusFixture, pending_ops: 7 })));
    renderApp(<SettingsSection />, { route: "/settings" });
    expect(await screen.findByRole("button", { name: "Flush queue (7)" })).toBeInTheDocument();
  });

  it("runs prune, and says it deletes nothing", async () => {
    const { user } = renderApp(<SettingsSection />, { route: "/settings" });
    await screen.findByText("/home/brain/graph.db");
    expect(screen.getByText(/consolidates old captures and deletes nothing/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Prune" }));
    await waitFor(() => expect(writes.some((w) => w.path === "/api/prune")).toBe(true));
  });

  it("reports an unreachable master rather than staying quiet", async () => {
    failWith("post", "/api/neo4j-test", 400, "connection refused");
    const { user } = renderApp(<SettingsSection />, { route: "/settings" });
    await screen.findByText("/home/brain/graph.db");
    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/Testing failed: connection refused/)).toBeInTheDocument();
  });
});
