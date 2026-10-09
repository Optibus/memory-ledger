import { describe, expect, it } from "vitest";
import { agentThreadFixture } from "@/test/fixtures";
import { blocksOf } from "./blocks";

const [user, agent] = agentThreadFixture.messages;

describe("blocksOf", () => {
  it("lists what the agent said, in order, and the reply once, never the tool logs or your own message", () => {
    const blocks = blocksOf([user!, agent!]);
    expect(blocks.map((b) => b.text)).toEqual([
      "Let me look for permits.",
      "The parking permit, by Oct 5. Should I add a task?",
    ]);
  });

  it("adds the reply when it was not said along the way, under its own key", () => {
    const blocks = blocksOf([{ ...agent!, events: [], text: "Done." }]);
    expect(blocks).toEqual([{ key: "a1:reply", text: "Done." }]);
  });

  it("keeps a block's key while the reply grows", () => {
    const early = blocksOf([{ ...agent!, status: "running", text: "", events: agent!.events.slice(0, 2) }]);
    const later = blocksOf([agent!]);
    expect(later[0]!.key).toBe(early[0]!.key);
  });

  it("does not read a failed reply", () => {
    expect(blocksOf([{ ...agent!, events: [], status: "error", text: "partial text", error: "boom" }])).toEqual([]);
  });
});
