import { describe, expect, it } from "vitest";
import { segments } from "./segments";
import type { AgentEvent } from "@/api/types";

describe("segments", () => {
  it("drops the last said text when it is the reply minus its [[next:]] marker", () => {
    const text = { kind: "text", output: "Done.\n\n[[next: ok]]" } as AgentEvent;
    expect(segments([text], true, "Done.")).toEqual([]);
  });
});
