import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { UsageMeter } from "./UsageMeter";

const summary = {
  ok: true, estimated: true, fiveHours: 1.5, today: 12.34, week: 100, month: 400,
  year: 3000, total: 3741.83, weeks: 6, coarseWeeks: 5,
};

describe("UsageMeter", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows 5h and today, and opens the longer windows on click", async () => {
    vi.spyOn(api, "usageSummary").mockResolvedValue(summary);
    render(<UsageMeter active />);
    await waitFor(() => expect(screen.getByText("$12.34")).toBeInTheDocument());
    expect(screen.getByText("$1.50")).toBeInTheDocument();
    expect(screen.queryByText("Year")).toBeNull();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Year")).toBeInTheDocument();
    expect(screen.getByText("$3,741.83")).toBeInTheDocument();
  });

  it("stays out of the way when the server fails", async () => {
    const spy = vi.spyOn(api, "usageSummary").mockRejectedValue(new Error("down"));
    const { container } = render(<UsageMeter active />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
