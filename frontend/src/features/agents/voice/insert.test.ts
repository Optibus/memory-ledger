import { describe, expect, it } from "vitest";
import { applySession, sessionText } from "./insert";

describe("applySession", () => {
  it("adds the session at the end, with a space only when one is needed", () => {
    expect(applySession("", "", "hello")).toBe("hello");
    expect(applySession("typed", "", "hello")).toBe("typed hello");
    expect(applySession("typed ", "", "hello")).toBe("typed hello");
  });

  it("replaces the previous partial with the next one, wherever typing has moved it", () => {
    expect(applySession("typed hel", "hel", "hello wor")).toBe("typed hello wor");
    expect(applySession("my own typed hello wor and after", "hello wor", "hello world")).toBe("my own typed hello world and after");
  });

  it("puts the text at the end when the person deleted what was inserted", () => {
    expect(applySession("kept", "gone", "new")).toBe("kept new");
  });

  it("takes back exactly what was dictated, and the space before it", () => {
    expect(applySession("typed hello world", "hello world", "")).toBe("typed");
    expect(applySession("hello world", "hello world", "")).toBe("");
    expect(applySession("typed", "", "")).toBe("typed");
  });

  it("joins finals and the open partial", () => {
    expect(sessionText(["one ", "two"], " three")).toBe("one two three");
    expect(sessionText([], "")).toBe("");
  });
});
