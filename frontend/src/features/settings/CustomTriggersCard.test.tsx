import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { CustomTriggersCard } from "./CustomTriggersCard";
import { renderApp } from "@/test/utils";
import { writes } from "@/test/server";

describe("CustomTriggersCard", () => {
  it("lists the triggers with their last run, the secret names and whether they run here", async () => {
    renderApp(<CustomTriggersCard writable />, { route: "/settings" });
    expect(await screen.findByText("site-health")).toBeInTheDocument();
    expect(screen.getByText("scheduled here")).toBeInTheDocument();
    expect(screen.getByText(/STATUS_TOKEN/)).toBeInTheDocument();
    expect(screen.getAllByText("broken.yaml: not valid YAML").length).toBeGreaterThan(0);
    expect(screen.getByRole("checkbox", { name: "broken on" })).toBeDisabled();
  });

  it("switches a trigger on", async () => {
    const { user } = renderApp(<CustomTriggersCard writable />, { route: "/settings" });
    await user.click(await screen.findByRole("checkbox", { name: "site-health on" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/custom-trigger-enable")?.body).toEqual({
        id: "site-health",
        enabled: true,
      }),
    );
  });

  it("opens the file, validates and saves it, dry-runs the saved one, and shows its runs", async () => {
    const { user } = renderApp(<CustomTriggersCard writable />, { route: "/settings" });
    await user.click((await screen.findAllByRole("button", { name: "Edit" }))[0]);
    const editor = await screen.findByLabelText("Trigger file");
    expect((editor as HTMLTextAreaElement).value).toContain("id: site-health");
    expect(screen.getByRole("table", { name: "runs" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Dry run" }));
    expect(await screen.findByText(/1 event\(s\) it would create; nothing was created/)).toBeInTheDocument();
    expect(screen.getByText("site-health:503")).toBeInTheDocument();

    await user.clear(editor);
    await user.type(editor, "id: site-health{enter}interval: 1s{enter}");
    expect(screen.getByRole("button", { name: "Dry run" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Validate" }));
    expect(await screen.findByText("interval must be at least 30s")).toBeInTheDocument();
    expect(writes.filter((w) => w.path === "/api/custom-trigger").every((w) => (w.body as { check_only: boolean }).check_only)).toBe(true);

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(writes.some((w) => w.path === "/api/custom-trigger" && !(w.body as { check_only: boolean }).check_only)).toBe(true),
    );
  });

  it("starts a new trigger from the template", async () => {
    const { user } = renderApp(<CustomTriggersCard writable />, { route: "/settings" });
    await user.type(await screen.findByLabelText("New trigger id"), "queue-depth");
    await user.click(screen.getByRole("button", { name: "New trigger" }));
    const editor = await screen.findByLabelText("Trigger file");
    expect((editor as HTMLTextAreaElement).value).toContain("id: queue-depth");
    expect(screen.getByText(/new, not saved/)).toBeInTheDocument();
  });

  it("is read-only on a read-only server", async () => {
    renderApp(<CustomTriggersCard writable={false} />, { route: "/settings" });
    expect(await screen.findByRole("checkbox", { name: "site-health on" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "New trigger" })).not.toBeInTheDocument();
  });
});
