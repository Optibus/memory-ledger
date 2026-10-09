import { useEffect, useRef, useState } from "react";
import type { AgentMessage } from "@/api/types";
import { blocksOf } from "./blocks";
import { speech } from "./speech";
import { translateBlock } from "./translate";

export type TurnEnd = { id: string; status: "ok" | "error" | "cancelled" };

/** What was already seen per conversation, shared so leaving a chat and coming back never re-reads it. */
const SEEN = new Map<string, Set<string>>();

export interface SpeakOptions {
  id: string;
  title: string;
  messages: AgentMessage[] | null;
  turnEnd: TurnEnd | null | undefined;
  /** Speak the new blocks (TTS on for this chat, and allowed to speak from here). */
  enabled: boolean;
  /** Translate each block into this language first. */
  translateTo: string | null;
  /** Called, in order after its blocks are queued, when a turn ends. */
  onSettled?: (end: TurnEnd) => void;
}

/**
 * Feeds the speech queue with each new block of a conversation, as the poll brings it.
 *
 * The first look at a conversation only records what is already there: opening a chat
 * never reads its history aloud. Blocks that arrive while speech is off are recorded
 * too, so turning it on later starts from now.
 *
 * Returns the turn end only once every block before it has been queued (translations
 * are asynchronous), which is what lets the conversation mode trust "turn ended and
 * the queue is empty" without a race between the two.
 */
export function useSpeakBlocks(o: SpeakOptions): TurnEnd | null {
  const [settled, setSettled] = useState<TurnEnd | null>(null);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const live = useRef(o);
  const lastEnd = useRef("");
  live.current = o;

  useEffect(() => {
    if (!o.id || !o.messages) return;
    const blocks = blocksOf(o.messages);
    const first = !SEEN.has(o.id);
    const seen = SEEN.get(o.id) ?? new Set<string>();
    SEEN.set(o.id, seen);
    const fresh = blocks.filter((b) => !seen.has(b.key));
    fresh.forEach((b) => seen.add(b.key));
    const speak = !first && o.enabled ? fresh : [];
    const end = o.turnEnd ?? null;
    chain.current = chain.current.then(async () => {
      for (const b of speak) {
        const { translateTo, id, title } = live.current;
        const text = translateTo ? await translateBlock(b.text, translateTo) : b.text;
        speech.enqueue({ chat: id, chatTitle: title, text });
      }
      if (end && end.id !== lastEnd.current) {
        lastEnd.current = end.id;
        setSettled(end);
        live.current.onSettled?.(end);
      }
    });
  }, [o.id, o.messages, o.turnEnd]); // eslint-disable-line react-hooks/exhaustive-deps

  return settled;
}
