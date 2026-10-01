import { describe, expect, it } from "vitest";
import { applyMark, parseRich, plainText, safeHref } from "./richText";

describe("parseRich", () => {
  it("reads the marks people type", () => {
    expect(parseRich("a **b** *c* _d_ __e__ ~~f~~ `g`")).toEqual([
      { t: "text", v: "a " },
      { t: "b", c: [{ t: "text", v: "b" }] },
      { t: "text", v: " " },
      { t: "i", c: [{ t: "text", v: "c" }] },
      { t: "text", v: " " },
      { t: "i", c: [{ t: "text", v: "d" }] },
      { t: "text", v: " " },
      { t: "u", c: [{ t: "text", v: "e" }] },
      { t: "text", v: " " },
      { t: "s", c: [{ t: "text", v: "f" }] },
      { t: "text", v: " " },
      { t: "code", v: "g" },
    ]);
  });

  it("nests, and a mark inside code is text", () => {
    expect(parseRich("**bold _and italic_**")).toEqual([
      { t: "b", c: [{ t: "text", v: "bold " }, { t: "i", c: [{ t: "text", v: "and italic" }] }] },
    ]);
    expect(parseRich("`**not bold**`")).toEqual([{ t: "code", v: "**not bold**" }]);
  });

  it("leaves what is not a mark alone", () => {
    for (const text of ["snake_case_name", "2*3*4", "a ** b", "unclosed **bold", "* list item"]) {
      expect(plainText(text)).toBe(text);
      expect(parseRich(text).every((n) => n.t === "text")).toBe(true);
    }
  });

  it("links bare URLs, without the punctuation after them", () => {
    expect(parseRich("see https://x.example/a?b=1. ok")).toEqual([
      { t: "text", v: "see " },
      { t: "link", href: "https://x.example/a?b=1", c: [{ t: "text", v: "https://x.example/a?b=1" }] },
      { t: "text", v: ". ok" },
    ]);
    expect(parseRich("www.example.com")[0]).toMatchObject({ t: "link", href: "https://www.example.com" });
  });

  it("reads Markdown and Slack links, and only http(s) or mailto", () => {
    expect(parseRich("[the deck](https://d.example/1)")).toEqual([
      { t: "link", href: "https://d.example/1", c: [{ t: "text", v: "the deck" }] },
    ]);
    expect(parseRich("<https://s.example|Slack link>")[0]).toMatchObject({
      t: "link",
      href: "https://s.example",
      c: [{ t: "text", v: "Slack link" }],
    });
    expect(plainText("[x](javascript:alert(1))")).toBe("[x](javascript:alert(1))");
    expect(parseRich("[x](javascript:alert(1))").some((n) => n.t === "link")).toBe(false);
    expect(safeHref("javascript:alert(1)")).toBeNull();
  });

  it("reads code fences as blocks", () => {
    expect(parseRich("run:\n```sh\nmake test\n```\ndone")).toEqual([
      { t: "text", v: "run:\n" },
      { t: "pre", v: "make test" },
      { t: "text", v: "\ndone" },
    ]);
  });

  it("plainText takes the markers out for a preview", () => {
    expect(plainText("**Done**: see [PR](https://g.example/1) _sent by Alex's brain_")).toBe(
      "Done: see PR sent by Alex's brain",
    );
  });
});

describe("applyMark", () => {
  it("wraps the selection and keeps it selected", () => {
    expect(applyMark("say hi now", 4, 6, "b")).toEqual({ text: "say **hi** now", start: 6, end: 8 });
    expect(applyMark("hi", 0, 2, "u")).toEqual({ text: "__hi__", start: 2, end: 4 });
  });

  it("puts an empty pair at the caret", () => {
    expect(applyMark("ab", 1, 1, "i")).toEqual({ text: "a**b", start: 2, end: 2 });
  });

  it("takes a mark off again", () => {
    expect(applyMark("say **hi** now", 6, 8, "b")).toEqual({ text: "say hi now", start: 4, end: 6 });
  });

  it("links: the URL is selected to type over, or a selected URL becomes the target", () => {
    const r = applyMark("the deck", 4, 8, "link");
    expect(r.text).toBe("the [deck](https://)");
    expect(r.text.slice(r.start, r.end)).toBe("https://");
    const u = applyMark("https://d.example", 0, 17, "link");
    expect(u.text).toBe("[link](https://d.example)");
    expect(u.text.slice(u.start, u.end)).toBe("link");
  });
});
