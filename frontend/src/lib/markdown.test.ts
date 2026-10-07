import { describe, expect, it } from "vitest";
import { parseBlocks, safeDocPath } from "./markdown";

describe("parseBlocks", () => {
  it("reads headings, lists, quotes and rules", () => {
    const b = parseBlocks("# Title\n\n- a\n- b\n\n1. x\n2. y\n\n> quoted\n\n---");
    expect(b.map((x) => x.t)).toEqual(["h", "list", "list", "quote", "hr"]);
    expect(b[1]).toEqual({ t: "list", ordered: false, items: ["a", "b"] });
    expect(b[2]).toMatchObject({ ordered: true, items: ["x", "y"] });
  });

  it("joins loose lines into one paragraph", () => {
    expect(parseBlocks("one\ntwo")).toEqual([{ t: "p", v: "one\ntwo" }]);
  });

  it("reads a table", () => {
    const b = parseBlocks("| a | b |\n|---|---|\n| 1 | 2 |");
    expect(b).toEqual([{ t: "table", head: ["a", "b"], rows: [["1", "2"]] }]);
  });

  it("leaves a code fence alone, whatever is inside", () => {
    const b = parseBlocks("```\n# not a heading\n- not a list\n```");
    expect(b).toHaveLength(1);
    expect(b[0].t).toBe("p");
  });

  it("turns a doc link on its own line into a card", () => {
    expect(parseBlocks("[Report](doc:documents/r.html)")).toEqual([
      { t: "doc", name: "Report", path: "documents/r.html" },
    ]);
  });

  it("refuses a doc path that leaves exports", () => {
    expect(safeDocPath("../x.md")).toBe(false);
    expect(safeDocPath("/etc/passwd")).toBe(false);
    expect(parseBlocks("[x](doc:../x.md)")[0].t).toBe("p");
  });
});
