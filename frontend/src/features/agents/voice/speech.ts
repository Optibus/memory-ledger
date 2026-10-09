import { useSyncExternalStore } from "react";
import { api } from "@/api/client";

/** One block of text for the voice to read. */
export interface Block {
  /** The conversation it came from; a change of conversation is announced by name. */
  chat: string;
  chatTitle: string;
  text: string;
  /** Skip the announcement of the chat name even when the chat changed. */
  quiet?: boolean;
}

export interface SpeechState {
  /** Audio is playing or being made. */
  speaking: boolean;
  /** Blocks waiting behind the one playing. */
  pending: number;
  /** Nothing playing and nothing queued: the microphone may open. */
  idle: boolean;
  error: string;
}

interface Item {
  audio: Promise<Blob | null>;
}

const ANNOUNCE: Record<string, string> = { pt: "Chat", en: "Chat", es: "Chat", it: "Chat" };

/**
 * The one speech queue of the whole app: FIFO, one voice at a time, for every chat.
 *
 * Each block starts being synthesised the moment it is queued, so the next one is
 * ready when the current one ends and there is no gap between them. Stopping
 * clears the queue and cuts the audio at once; it never touches the agent.
 */
export class SpeechQueue {
  private items: Item[] = [];
  private playing = false;
  private audio: HTMLAudioElement | null = null;
  private release: (() => void) | null = null;
  private last = "";
  private epoch = 0;
  private error = "";
  private listeners = new Set<() => void>();
  private cached: SpeechState = { speaking: false, pending: 0, idle: true, error: "" };

  /** Called with the announcement text for a chat change; the language follows the server's detection. */
  enqueue(block: Block): void {
    const body = block.text.trim();
    if (!body) return;
    const named = !block.quiet && this.last !== block.chat;
    this.last = block.chat;
    const text = named ? `${ANNOUNCE.pt} ${block.chatTitle || "sem nome"}: ${body}` : body;
    this.error = "";
    const epoch = this.epoch;
    const audio = api.speak(text).catch((e: unknown) => {
      if (epoch === this.epoch) this.error = e instanceof Error ? e.message : String(e);
      return null;
    });
    this.items.push({ audio });
    this.emit();
    void this.pump();
  }

  /** Stop talking: the audio in progress and everything waiting. */
  interrupt(): void {
    this.epoch += 1;
    this.items = [];
    this.audio?.pause();
    this.release?.();
    this.emit();
  }

  state(): SpeechState {
    return this.cached;
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private emit() {
    const next: SpeechState = {
      speaking: this.playing,
      pending: this.items.length,
      idle: !this.playing && this.items.length === 0,
      error: this.error,
    };
    const c = this.cached;
    if (c.speaking === next.speaking && c.pending === next.pending && c.error === next.error) return;
    this.cached = next;
    this.listeners.forEach((fn) => fn());
  }

  private async pump(): Promise<void> {
    if (this.playing) return;
    const item = this.items.shift();
    if (!item) {
      this.emit();
      return;
    }
    this.playing = true;
    const epoch = this.epoch;
    this.emit();
    try {
      const blob = await item.audio;
      if (blob && epoch === this.epoch) await this.play(blob);
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.playing = false;
      this.audio = null;
      this.release = null;
      this.emit();
      void this.pump();
    }
  }

  private play(blob: Blob): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(blob);
      const el = new Audio(url);
      this.audio = el;
      const done = () => {
        URL.revokeObjectURL(url);
        this.release = null;
        resolve();
      };
      this.release = done;
      el.onended = done;
      el.onerror = () => {
        URL.revokeObjectURL(url);
        this.release = null;
        reject(new Error("the browser could not play the audio"));
      };
      el.play().catch((e: unknown) => {
        URL.revokeObjectURL(url);
        this.release = null;
        reject(e instanceof Error ? e : new Error(String(e)));
      });
    });
  }
}

export const speech = new SpeechQueue();

export function useSpeech(): SpeechState {
  return useSyncExternalStore(speech.subscribe, () => speech.state());
}
