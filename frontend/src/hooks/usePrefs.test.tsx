import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { PrefsProvider, usePref } from "./usePrefs";
import { resetWrites, server, writes } from "@/test/server";

beforeEach(() => {
  resetWrites();
  window.localStorage.clear();
});

/** A component that reads and writes one preference, for the tests to drive. */
function Probe({ prefKey = "view", fallback = "agenda" }: { prefKey?: string; fallback?: string }) {
  const [value, setValue, ready] = usePref<string>(prefKey, fallback);
  return (
    <div>
      <span data-testid="value">{value}</span>
      <span data-testid="ready">{ready ? "ready" : "waiting"}</span>
      <button onClick={() => setValue("closed")}>change</button>
    </div>
  );
}

const renderProbe = (ui = <Probe />) => render(<PrefsProvider>{ui}</PrefsProvider>);

describe("usePref", () => {
  it("starts from the fallback when nothing has been stored", async () => {
    renderProbe();
    expect(screen.getByTestId("value")).toHaveTextContent("agenda");
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("ready"));
  });

  it("keeps a change across a remount, which is the whole point", async () => {
    const user = userEvent.setup();
    const first = renderProbe();
    await user.click(screen.getByRole("button", { name: "change" }));
    expect(screen.getByTestId("value")).toHaveTextContent("closed");
    first.unmount();

    renderProbe();
    expect(screen.getByTestId("value")).toHaveTextContent("closed");
  });

  it("writes through to localStorage synchronously, so a reload right after a click keeps it", async () => {
    const user = userEvent.setup();
    renderProbe();
    await user.click(screen.getByRole("button", { name: "change" }));
    expect(JSON.parse(window.localStorage.getItem("ml:prefs") || "{}")).toMatchObject({
      view: "closed",
    });
  });

  it("does not push to the brain while syncing is off", async () => {
    const user = userEvent.setup();
    renderProbe();
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("ready"));
    await user.click(screen.getByRole("button", { name: "change" }));
    await new Promise((r) => setTimeout(r, 1000));   // past the push debounce
    expect(writes.some((w) => w.path === "/api/ui-prefs")).toBe(false);
  });

  it("starts from the brain's copy when syncing is on", async () => {
    server.use(
      http.get("/api/ui-prefs", () =>
        HttpResponse.json({ ok: true, synced: true, path: "/p", prefs: { view: "week" } }),
      ),
    );
    renderProbe();
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("week"));
  });

  it("keeps a local key the brain has never heard of", async () => {
    window.localStorage.setItem("ml:prefs", JSON.stringify({ other: "kept" }));
    server.use(
      http.get("/api/ui-prefs", () =>
        HttpResponse.json({ ok: true, synced: true, path: "/p", prefs: { view: "week" } }),
      ),
    );
    renderProbe(<Probe prefKey="other" fallback="gone" />);
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("kept"));
  });

  it("pushes a change to the brain, once, when syncing is on", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/ui-prefs", () =>
        HttpResponse.json({ ok: true, synced: true, path: "/p", prefs: {} }),
      ),
    );
    renderProbe();
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("ready"));
    await user.click(screen.getByRole("button", { name: "change" }));

    await waitFor(
      () => expect(writes.filter((w) => w.path === "/api/ui-prefs").length).toBe(1),
      { timeout: 3000 },
    );
    expect(writes.find((w) => w.path === "/api/ui-prefs")?.body).toMatchObject({
      prefs: { view: "closed" },
    });
  });

  it("keeps working when the brain cannot be reached at all", async () => {
    server.use(http.get("/api/ui-prefs", () => HttpResponse.error()));
    const user = userEvent.setup();
    renderProbe();
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("ready"));
    await user.click(screen.getByRole("button", { name: "change" }));
    expect(screen.getByTestId("value")).toHaveTextContent("closed");
  });

  it("survives localStorage being unavailable, rather than taking the page down", async () => {
    const getItem = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new DOMException("denied");
      });
    const setItem = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new DOMException("denied");
      });
    try {
      const user = userEvent.setup();
      renderProbe();
      expect(screen.getByTestId("value")).toHaveTextContent("agenda");
      await user.click(screen.getByRole("button", { name: "change" }));
      expect(screen.getByTestId("value")).toHaveTextContent("closed");
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
    }
  });

  it("ignores a corrupt stored blob instead of rendering nothing", async () => {
    window.localStorage.setItem("ml:prefs", "{not json");
    renderProbe();
    expect(screen.getByTestId("value")).toHaveTextContent("agenda");
  });

  it("refuses to run outside the provider, rather than silently forgetting", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() => render(<Probe />)).toThrow(/PrefsProvider/);
    } finally {
      quiet.mockRestore();
    }
  });

  it("starts syncing when the Settings page says the switch was flipped", async () => {
    const user = userEvent.setup();
    renderProbe();
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("ready"));

    act(() => {
      window.dispatchEvent(new CustomEvent("ml:ui-sync", { detail: true }));
    });
    await user.click(screen.getByRole("button", { name: "change" }));

    await waitFor(
      () => expect(writes.some((w) => w.path === "/api/ui-prefs")).toBe(true),
      { timeout: 3000 },
    );
  });
});
