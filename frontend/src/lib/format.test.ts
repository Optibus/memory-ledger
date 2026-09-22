import { describe, expect, it } from "vitest";
import { bytes, formatDate, humanize, isOverdue, pluralize, toDateInput, truncate } from "./format";

describe("formatDate", () => {
  it("is empty for nothing, rather than 'Invalid Date'", () => {
    expect(formatDate("")).toBe("");
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
  });

  it("gives an unparseable value back rather than swallowing it", () => {
    expect(formatDate("not a date")).toBe("not a date");
  });

  it("survives a timezone this machine has never heard of", () => {
    expect(() => formatDate("2026-09-22T12:00:00Z", "Mars/Olympus")).not.toThrow();
    expect(formatDate("2026-09-22T12:00:00Z", "Mars/Olympus")).toBe("2026-09-22");
  });
});

describe("toDateInput", () => {
  it("trims a stored timestamp to the day a date input wants", () => {
    expect(toDateInput("2026-09-22T12:00:00Z")).toBe("2026-09-22");
  });
  it("is empty for anything unusable", () => {
    expect(toDateInput("")).toBe("");
    expect(toDateInput("nope")).toBe("");
  });
});

describe("isOverdue", () => {
  it("compares whole days against the brain's own today", () => {
    expect(isOverdue("2026-09-20T12:00:00", "2026-09-22")).toBe(true);
    expect(isOverdue("2026-09-22T23:00:00", "2026-09-22")).toBe(false);
    expect(isOverdue("2026-09-30", "2026-09-22")).toBe(false);
  });
  it("is never overdue with no due date", () => {
    expect(isOverdue("", "2026-09-22")).toBe(false);
  });
});

describe("small text helpers", () => {
  it("pluralizes on the count, not on the word", () => {
    expect(pluralize(1, "node")).toBe("1 node");
    expect(pluralize(0, "node")).toBe("0 nodes");
    expect(pluralize(2, "entry", "entries")).toBe("2 entries");
  });

  it("truncates with an ellipsis only when it has to", () => {
    expect(truncate("short", 10)).toBe("short");
    expect(truncate("a longer string", 8)).toBe("a longe…");
  });

  it("humanizes an id fragment", () => {
    expect(humanize("set-due")).toBe("Set due");
    expect(humanize("map_poll_seconds")).toBe("Map poll seconds");
    expect(humanize("")).toBe("");
  });
});

describe("bytes", () => {
  it("counts small sizes exactly", () => {
    expect(bytes(0)).toBe("0 B");
    expect(bytes(512)).toBe("512 B");
  });

  it("uses binary units, so a 100K file does not read as 102 KB", () => {
    expect(bytes(102400)).toBe("100 KB");
    expect(bytes(1024)).toBe("1.0 KB");
  });

  it("keeps one decimal only while it carries information", () => {
    expect(bytes(1536)).toBe("1.5 KB");
    expect(bytes(20 * 1024 * 1024)).toBe("20 MB");
  });

  it("refuses to invent a size for a value that is not one", () => {
    expect(bytes(-1)).toBe("—");
    expect(bytes(Number.NaN)).toBe("—");
  });
});
