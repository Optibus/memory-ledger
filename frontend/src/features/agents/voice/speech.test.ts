import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { SpeechQueue } from "./speech";

const played: string[] = [];
let ends: (() => void)[] = [];

class FakeAudio {
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public src: string) {}
  play() {
    played.push(this.src);
    ends.push(() => this.onended?.());
    return Promise.resolve();
  }
  pause() {}
}

const flush = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};

beforeEach(() => {
  played.length = 0;
  ends = [];
  vi.stubGlobal("Audio", FakeAudio);
  URL.createObjectURL = (b: Blob | MediaSource) => `blob:${(b as Blob).size}`;
  URL.revokeObjectURL = () => {};
  vi.spyOn(api, "speak").mockImplementation(async (text: string) => new Blob([text]));
});
afterEach(() => vi.restoreAllMocks());

describe("SpeechQueue", () => {
  it("speaks in order, one at a time, and is idle only when nothing plays or waits", async () => {
    const q = new SpeechQueue();
    expect(q.state().idle).toBe(true);
    q.enqueue({ chat: "a", chatTitle: "A", text: "one" });
    q.enqueue({ chat: "a", chatTitle: "A", text: "two" });
    await flush();
    expect(played).toHaveLength(1);
    expect(q.state()).toMatchObject({ speaking: true, pending: 1, idle: false });
    ends.shift()!();
    await flush();
    expect(played).toHaveLength(2);
    ends.shift()!();
    await flush();
    expect(q.state().idle).toBe(true);
    expect((api.speak as unknown as { mock: { calls: string[][] } }).mock.calls.map((c) => c[0])).toEqual(["Chat A: one", "two"]);
  });

  it("names the chat when it changes, including when coming back to an earlier one", async () => {
    const q = new SpeechQueue();
    for (const [chat, title, text] of [["x", "X", "1"], ["x", "X", "2"], ["y", "Y", "3"], ["x", "X", "4"]]) {
      q.enqueue({ chat: chat!, chatTitle: title!, text: text! });
    }
    const spoken = (api.speak as unknown as { mock: { calls: string[][] } }).mock.calls.map((c) => c[0]);
    expect(spoken).toEqual(["Chat X: 1", "2", "Chat Y: 3", "Chat X: 4"]);
  });

  it("interrupt cuts the audio and clears the queue, and what is queued next still plays", async () => {
    const q = new SpeechQueue();
    q.enqueue({ chat: "a", chatTitle: "A", text: "one" });
    q.enqueue({ chat: "a", chatTitle: "A", text: "two" });
    await flush();
    q.interrupt();
    await flush();
    expect(q.state()).toMatchObject({ pending: 0, idle: true });
    expect(played).toHaveLength(1);
    q.enqueue({ chat: "a", chatTitle: "A", text: "three" });
    await flush();
    expect(played).toHaveLength(2);
  });

  it("skips a block that cleans down to nothing, and survives one the engine refuses", async () => {
    vi.spyOn(api, "speak").mockReset().mockResolvedValueOnce(null).mockRejectedValueOnce(new Error("no engine")).mockResolvedValue(new Blob(["ok"]));
    const q = new SpeechQueue();
    q.enqueue({ chat: "a", chatTitle: "A", text: "x", quiet: true });
    q.enqueue({ chat: "a", chatTitle: "A", text: "y", quiet: true });
    q.enqueue({ chat: "a", chatTitle: "A", text: "z", quiet: true });
    await flush();
    expect(played).toHaveLength(1);
    ends.shift()!();
    await flush();
    expect(q.state().idle).toBe(true);
  });
});
