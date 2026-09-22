import { describe, expect, it } from "vitest";
import { initialState, toQuery } from "./state";

describe("toQuery", () => {
  it("sends nothing at all for a bare agenda", () => {
    expect(toQuery(initialState())).toEqual({});
  });

  it("sends only what the chosen view reads", () => {
    const range = { ...initialState("range"), from: "2026-09-01", to: "2026-09-30", status: "open" };
    expect(toQuery(range)).toEqual({ from: "2026-09-01", to: "2026-09-30", status: "open" });
    // `since_days` belongs to `closed` and must not ride along.
    expect(toQuery(range)).not.toHaveProperty("since_days");
  });

  it("sends since_days only on closed", () => {
    expect(toQuery({ ...initialState("closed"), sinceDays: 14 })).toEqual({ since_days: "14" });
  });

  it("joins multi-select facets as the comma lists the server expects", () => {
    const s = { ...initialState(), realms: ["optibus", "shared"], kinds: ["postmortem"], assignees: ["gabriel"] };
    expect(toQuery(s)).toEqual({
      realm: "optibus,shared",
      kind: "postmortem",
      assignee: "gabriel",
    });
  });

  it("omits a facet with nothing selected, which means every value", () => {
    expect(toQuery({ ...initialState(), realms: [] })).not.toHaveProperty("realm");
  });

  it("sends mine as the flag the CLI uses", () => {
    expect(toQuery({ ...initialState(), mine: true })).toEqual({ mine: "1" });
  });
});

describe("initialState", () => {
  it("gives the range view a usable two-week window rather than empty dates", () => {
    const s = initialState("range");
    expect(s.from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(s.to > s.from).toBe(true);
  });
});
