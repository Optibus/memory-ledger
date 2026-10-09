import { useCallback, useEffect, useRef, useState } from "react";
import type { VoiceConfig } from "@/api/types";
import { startDictation } from "./dictation";
import type { Dictation } from "./dictation";
import { applySession, sessionText } from "./insert";
import { useSpeech } from "./speech";
import type { SpeechState } from "./speech";
import type { TurnEnd } from "./useSpeakBlocks";

/**
 * idle      nothing is listening
 * dictation the microphone is on for one dictation; Stop keeps the text, Cancel takes it back
 * listening conversation mode, microphone on
 * waiting   conversation mode, message sent, microphone off until the turn ended and the voice is quiet
 *           (the voice speaking is part of waiting: new blocks can arrive while it talks)
 */
export type VoicePhase = "idle" | "dictation" | "listening" | "waiting";

export interface VoiceSessionOptions {
  config: VoiceConfig | null;
  /** The composer's text right now, and a way to change it from what it is now. */
  getText: () => string;
  update: (fn: (current: string) => string) => void;
  /** Send the composer's text; the id of the reply it started, or "" when it did not go. */
  send: () => Promise<string>;
  /** The latest turn end, once every block before it has been queued. */
  settled: TurnEnd | null;
  onError: (message: string | null) => void;
  focus: () => void;
}

export interface VoiceSession {
  phase: VoicePhase;
  speech: SpeechState;
  conversation: boolean;
  startDictation: () => void;
  stop: () => void;
  cancel: () => void;
  setConversation: (on: boolean) => void;
}

/**
 * The voice state machine of one composer. See `VoicePhase`; the rules it keeps:
 *  - conversation mode sends on `silence_s` seconds without new text, only when there is text;
 *  - the microphone is shut from the send until the turn ended AND the voice has nothing left to say;
 *  - an end of any kind (done, error, cancelled) reopens it, so it can never wait forever;
 *  - leaving the chat is a Stop: what was heard stays in the draft.
 */
export function useVoiceSession(o: VoiceSessionOptions): VoiceSession {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [conversation, setConv] = useState(false);
  const speech = useSpeech();
  const live = useRef(o);
  live.current = o;
  const handle = useRef<Dictation | null>(null);
  const sess = useRef({ finals: [] as string[], partial: "", prev: "", discard: false });
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const ended = useRef<(() => void) | null>(null);
  const waitingFor = useRef("");
  const convRef = useRef(false);
  const starting = useRef(false);
  const unmounted = useRef(false);

  const clearTimer = () => clearTimeout(timer.current);

  const show = useCallback(() => {
    const s = sess.current;
    const next = sessionText(s.finals, s.partial);
    live.current.update((v) => applySession(v, s.prev, next));
    s.prev = next;
  }, []);

  const begin = useCallback(
    async (mode: "dictation" | "listening") => {
      const cfg = live.current.config;
      if (!cfg || handle.current || starting.current) return;
      starting.current = true;
      live.current.onError(null);
      sess.current = { finals: [], partial: "", prev: "", discard: false };
      const arm = () => {
        if (mode !== "listening") return;
        clearTimer();
        timer.current = setTimeout(() => void silence(), cfg.silence_s * 1000);
      };
      const heard = (fn: () => void) => () => {
        if (sess.current.discard) return;
        fn();
        show();
        if (sessionText(sess.current.finals, sess.current.partial)) arm();
      };
      try {
        const h = await startDictation(cfg.stt_auto ? "auto" : cfg.stt_language, {
          onPartial: (t) => heard(() => (sess.current.partial = t))(),
          onFinal: (t) => heard(() => { sess.current.finals.push(t); sess.current.partial = ""; })(),
          onEnd: (reason) => {
            clearTimer();
            handle.current = null;
            const done = ended.current;
            ended.current = null;
            if (reason) live.current.onError(reason);
            if (done) return done();
            // Ended by itself (denied, lost, suspended): a Stop, and the mode is over.
            convRef.current = false;
            if (!unmounted.current) {
              setConv(false);
              setPhase("idle");
              live.current.focus();
            }
          },
        });
        handle.current = h;
        setPhase(mode);
      } catch (e) {
        convRef.current = false;
        setConv(false);
        setPhase("idle");
        live.current.onError(e instanceof Error ? e.message : String(e));
      } finally {
        starting.current = false;
      }
    },
    [show], // eslint-disable-line react-hooks/exhaustive-deps
  );

  /** Close the capture and wait for the server's last words, which are applied before this resolves. */
  const close = useCallback((discard: boolean) => {
    const h = handle.current;
    if (!h) return Promise.resolve();
    return new Promise<void>((resolve) => {
      ended.current = resolve;
      sess.current.discard = discard;
      if (discard) h.cancel();
      else h.stop();
    });
  }, []);

  async function silence() {
    if (!convRef.current || !handle.current) return;
    if (!sessionText(sess.current.finals, sess.current.partial) && !live.current.getText().trim()) return;
    await close(false);
    if (!convRef.current) return;
    const id = await live.current.send();
    if (id && convRef.current) {
      waitingFor.current = id;
      setPhase("waiting");
    } else {
      // Nothing went (send failed, shown by the composer): the mode ends rather than loops.
      convRef.current = false;
      setConv(false);
      setPhase("idle");
    }
  }

  // Waiting ends when the turn did AND the voice has nothing left; then listening again.
  useEffect(() => {
    if (phase !== "waiting" || !convRef.current) return;
    if (o.settled?.id === waitingFor.current && speech.idle) void begin("listening");
  }, [phase, o.settled, speech.idle, begin]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(
    () => () => {
      unmounted.current = true;
      convRef.current = false;
      clearTimer();
      handle.current?.stop();
    },
    [],
  );

  return {
    phase,
    speech,
    conversation,
    startDictation: () => void begin("dictation"),
    stop: () => {
      void close(false).then(() => {
        setPhase("idle");
        live.current.focus();
      });
    },
    cancel: () => {
      void close(true).then(() => {
        live.current.update((v) => applySession(v, sess.current.prev, ""));
        sess.current.prev = "";
        setPhase("idle");
        live.current.focus();
      });
    },
    setConversation: (on) => {
      convRef.current = on;
      setConv(on);
      if (on) void begin("listening");
      else {
        clearTimer();
        void close(false).then(() => setPhase("idle"));
        setPhase("idle");
      }
    },
  };
}
