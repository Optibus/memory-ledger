import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { UsageSection } from "./UsageSection";
import { lastWeeks, normalizeRange, reportFilename } from "./report";
import { renderApp } from "@/test/utils";
import { server } from "@/test/server";
import { usageWeeksFixture } from "@/test/fixtures";

afterEach(() => {
  vi.restoreAllMocks();
});

/** Every /api/usage-report query the section sent, in order. */
function watchQueries(): URLSearchParams[] {
  const seen: URLSearchParams[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname === "/api/usage-report") seen.push(url.searchParams);
  });
  return seen;
}

describe("Usage", () => {
  it("frames the server's report, sandboxed and without scripts", async () => {
    renderApp(<UsageSection />, { route: "/usage", withMeta: false });
    const frame = await screen.findByTitle("usage report");
    expect(frame).toHaveAttribute("srcdoc", expect.stringContaining("Where the Quota Went"));
    expect(frame).toHaveAttribute("srcdoc", expect.stringContaining("2026-W01,2026-W02,2026-W03"));
    const sandbox = frame.getAttribute("sandbox") ?? "";
    expect(sandbox).toContain("allow-same-origin");
    expect(sandbox).not.toContain("allow-scripts");
  });

  it("asks for the range picked in the week selectors", async () => {
    server.events.removeAllListeners();
    const seen = watchQueries();
    const { user } = renderApp(<UsageSection />, { route: "/usage", withMeta: false });
    await screen.findByTitle("usage report");
    await user.selectOptions(screen.getByLabelText("from week"), "2026-W02");
    await waitFor(() =>
      expect(screen.getByTitle("usage report")).toHaveAttribute("srcdoc", expect.stringContaining("<p>2026-W02,2026-W03</p>")),
    );
    expect(seen.at(-1)?.get("from")).toBe("2026-W02");
    await user.selectOptions(screen.getByLabelText("to week"), "2026-W02");
    await waitFor(() =>
      expect(screen.getByTitle("usage report")).toHaveAttribute("srcdoc", expect.stringContaining("<p>2026-W02</p>")),
    );
    await user.click(screen.getByRole("button", { name: "all" }));
    await waitFor(() =>
      expect(screen.getByTitle("usage report")).toHaveAttribute(
        "srcdoc",
        expect.stringContaining("<p>2026-W01,2026-W02,2026-W03</p>"),
      ),
    );
    server.events.removeAllListeners();
  });

  it("downloads the page as a self-contained HTML file", async () => {
    const created: Blob[] = [];
    const createObjectURL = vi.fn((b: Blob) => {
      created.push(b);
      return "blob:report";
    });
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    });
    const { user } = renderApp(<UsageSection />, { route: "/usage", withMeta: false });
    await screen.findByTitle("usage report");
    await user.click(screen.getByRole("button", { name: "Download HTML" }));
    expect(clicked).toEqual(["usage-report_2026-W01_2026-W03.html"]);
    // jsdom's Blob has no .text(); FileReader is the portable way in.
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(created[0]);
    });
    expect(text).toContain("Where the Quota Went");
    expect(created[0].type).toContain("text/html");
  });

  it("saves as PDF through the print dialog of the report alone", async () => {
    const { user } = renderApp(<UsageSection />, { route: "/usage", withMeta: false });
    const frame = (await screen.findByTitle("usage report")) as HTMLIFrameElement;
    const print = vi.fn();
    vi.spyOn(frame, "contentWindow", "get").mockReturnValue({ focus: vi.fn(), print } as unknown as Window);
    await user.click(screen.getByRole("button", { name: "Save as PDF" }));
    expect(print).toHaveBeenCalledTimes(1);
  });

  it("says why when the server refuses", async () => {
    server.use(
      http.get("/api/usage-report", () => HttpResponse.json({ ok: false, error: "not an ISO week tag" })),
    );
    renderApp(<UsageSection />, { route: "/usage", withMeta: false });
    expect(await screen.findByRole("alert")).toHaveTextContent("not an ISO week tag");
    expect(screen.getByRole("button", { name: "Download HTML" })).toBeDisabled();
  });
});

describe("usage range logic", () => {
  it("drops a bound for a week that is gone, and turns a backwards range round", () => {
    expect(normalizeRange({ from: "2025-W50", to: "2026-W02" }, usageWeeksFixture)).toEqual({
      from: "",
      to: "2026-W02",
    });
    expect(normalizeRange({ from: "2026-W03", to: "2026-W01" }, usageWeeksFixture)).toEqual({
      from: "2026-W01",
      to: "2026-W03",
    });
  });

  it("takes the newest N weeks, open at the newest end", () => {
    expect(lastWeeks(usageWeeksFixture, 2)).toEqual({ from: "2026-W02", to: "" });
    expect(lastWeeks(usageWeeksFixture, 10)).toEqual({ from: "2026-W01", to: "" });
    expect(lastWeeks([], 4)).toEqual({ from: "", to: "" });
  });

  it("names the file after the weeks it covers", () => {
    expect(reportFilename(["2026-W01", "2026-W03"])).toBe("usage-report_2026-W01_2026-W03.html");
    expect(reportFilename(["2026-W02"], "pdf")).toBe("usage-report_2026-W02.pdf");
    expect(reportFilename([])).toBe("usage-report.html");
  });
});
