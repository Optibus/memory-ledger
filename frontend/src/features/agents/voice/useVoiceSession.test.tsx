import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DictationEvents } from "./dictation";
import type { VoiceConfig } from "@/api/types";
import { speech } from "./speech";
import { useVoiceSession } from "./useVoiceSession";
import type { VoiceSessionOptions } from "./useVoiceSession";

let events: DictationEvents;
const stop = vi.fn();
const cancel = vi.fn();
const starts: string[] = [];

vi.mock("./dictation", () => ({
  dictationProblem: () => "",
  startDictation: vi.fn(async (language: string, ev: DictationEvents) => {
    starts.push(language);
    events = ev;
    return {
      stop: () => {
        stop();
        // The server finishes what it had buffered, then the session ends.
        queueMicrotask(() => ev.onEnd(""));
      },
      cancel: () => {
        cancel();
        queueMicrotask(() => ev.onEnd(""));
      },
    };
  }),
}));

const config: VoiceConfig = {
  stt_language: "pt", stt_auto: false, tts_language: "pt", tts_auto: true, translate: false,
  translate_target: "en", tts_enabled: true, tts_background: false, silence_s: 5, tts_voices: {}, verbosity: "default",
};

/** A composer's text box, as the hook sees it. */
function setup(over: Partial<VoiceSessionOptions> = {}, initial = "") {
  let text = initial;
  const send = vi.fn(async () => {
    if (!text.trim()) return "";
    text = "";
    return "reply-1";
  });
  const opts = (settled: VoiceSessionOptions["settled"]): VoiceSessionOptions => ({
    config,
    getText: () => text,
    update: (fn) => void (text = fn(text)),
    send,
    settled,
    onError: vi.fn(),
    focus: vi.fn(),
    ...over,
  });
  const hook = renderHook((p: { settled: VoiceSessionOptions["settled"] }) => useVoiceSession(opts(p.settled)), {
    initialProps: { settled: null as VoiceSessionOptions["settled"] },
  });
  return { hook, send, text: () => text };
}

const tick = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

beforeEach(() => {
  vi.useFakeTimers();
  stop.mockClear();
  cancel.mockClear();
  starts.length = 0;
  speech.interrupt();
});
afterEach(() => vi.useRealTimers());

describe("dictation", () => {
  it("shows the text while it is spoken, and Stop keeps it", async () => {
    const { hook, text } = setup();
    await act(async () => hook.result.current.startDictation());
    expect(hook.result.current.phase).toBe("dictation");
    expect(starts).toEqual(["pt"]);
    act(() => events.onPartial("olá mun", 0));
    expect(text()).toBe("olá mun");
    act(() => events.onFinal("olá mundo", 0, "pt"));
    act(() => events.onPartial("tudo", 1));
    expect(text()).toBe("olá mundo tudo");
    await act(async () => hook.result.current.stop());
    await tick();
    expect(hook.result.current.phase).toBe("idle");
    expect(text()).toBe("olá mundo tudo");
  });

  it("Cancel takes back what this session wrote and leaves what was typed before", async () => {
    // The person had typed something first.
    const { hook, text } = setup({}, "already typed");
    await act(async () => hook.result.current.startDictation());
    act(() => events.onFinal("ditado", 0, "pt"));
    expect(text()).toBe("already typed ditado");
    await act(async () => hook.result.current.cancel());
    await tick();
    expect(text()).toBe("already typed");
    expect(hook.result.current.phase).toBe("idle");
  });

  it("auto-detect asks the server for any language", async () => {
    const { hook } = setup({ config: { ...config, stt_auto: true } });
    await act(async () => hook.result.current.startDictation());
    expect(starts).toEqual(["auto"]);
  });

  it("a session that ends by itself (denied, lost) is a Stop, and says why", async () => {
    const onError = vi.fn();
    const { hook, text } = setup({ onError });
    await act(async () => hook.result.current.startDictation());
    act(() => events.onFinal("kept", 0, "pt"));
    act(() => events.onEnd("The connection to the transcriber was lost."));
    expect(hook.result.current.phase).toBe("idle");
    expect(text()).toBe("kept");
    expect(onError).toHaveBeenLastCalledWith("The connection to the transcriber was lost.");
  });
});

describe("conversation mode", () => {
  it("sends after the silence, shuts the microphone, and reopens it only after the turn AND the voice", async () => {
    const { hook, send } = setup();
    await act(async () => hook.result.current.setConversation(true));
    expect(hook.result.current.phase).toBe("listening");
    act(() => events.onPartial("quero saber", 0));
    await tick(4_000);
    expect(send).not.toHaveBeenCalled();
    act(() => events.onFinal("quero saber das tarefas", 0, "pt"));
    await tick(4_900);
    expect(send).not.toHaveBeenCalled();
    await tick(200);
    expect(send).toHaveBeenCalledTimes(1);
    expect(hook.result.current.phase).toBe("waiting");
    expect(stop).toHaveBeenCalled();
    expect(starts).toHaveLength(1);

    // The agent's blocks are being read when its turn ends: still waiting.
    vi.spyOn(speech, "state").mockReturnValue({ speaking: true, pending: 1, idle: false, error: "" });
    hook.rerender({ settled: { id: "reply-1", status: "ok" } });
    await tick(100);
    expect(starts).toHaveLength(1);
    vi.restoreAllMocks();
  });

  it("reopens when the turn ended and nothing is left to say", async () => {
    const { hook } = setup();
    await act(async () => hook.result.current.setConversation(true));
    act(() => events.onFinal("oi", 0, "pt"));
    await tick(5_100);
    expect(hook.result.current.phase).toBe("waiting");
    hook.rerender({ settled: { id: "an-older-turn", status: "ok" } });
    await tick(100);
    expect(starts).toHaveLength(1);
    hook.rerender({ settled: { id: "reply-1", status: "ok" } });
    await tick(100);
    expect(starts).toHaveLength(2);
    expect(hook.result.current.phase).toBe("listening");
  });

  it("an error or a cancelled turn also gives the microphone back", async () => {
    for (const status of ["error", "cancelled"] as const) {
      starts.length = 0;
      const { hook } = setup();
      await act(async () => hook.result.current.setConversation(true));
      act(() => events.onFinal("oi", 0, "pt"));
      await tick(5_100);
      hook.rerender({ settled: { id: "reply-1", status } });
      await tick(100);
      expect(starts).toHaveLength(2);
      hook.unmount();
    }
  });

  it("never sends an empty message: silence with no words keeps listening", async () => {
    const { hook, send } = setup();
    await act(async () => hook.result.current.setConversation(true));
    await tick(30_000);
    expect(send).not.toHaveBeenCalled();
    expect(hook.result.current.phase).toBe("listening");
  });

  it("turning it off while waiting ends the mode without touching the voice", async () => {
    const { hook } = setup();
    await act(async () => hook.result.current.setConversation(true));
    act(() => events.onFinal("oi", 0, "pt"));
    await tick(5_100);
    const interrupt = vi.spyOn(speech, "interrupt");
    await act(async () => hook.result.current.setConversation(false));
    await tick();
    expect(hook.result.current.phase).toBe("idle");
    hook.rerender({ settled: { id: "reply-1", status: "ok" } });
    await tick(100);
    expect(starts).toHaveLength(1);
    expect(interrupt).not.toHaveBeenCalled();
  });

  it("leaving the chat stops the capture and keeps the text", async () => {
    const { hook, text } = setup();
    await act(async () => hook.result.current.setConversation(true));
    act(() => events.onFinal("rascunho", 0, "pt"));
    hook.unmount();
    expect(stop).toHaveBeenCalled();
    expect(text()).toBe("rascunho");
  });

  it("the silence is the configured one", async () => {
    const { hook, send } = setup({ config: { ...config, silence_s: 2 } });
    await act(async () => hook.result.current.setConversation(true));
    act(() => events.onFinal("oi", 0, "pt"));
    await tick(2_100);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("when the send fails the mode ends instead of looping", async () => {
    const { hook } = setup({ send: vi.fn(async () => "") });
    await act(async () => hook.result.current.setConversation(true));
    act(() => events.onFinal("oi", 0, "pt"));
    await tick(5_100);
    expect(hook.result.current.phase).toBe("idle");
    expect(hook.result.current.conversation).toBe(false);
  });
});
