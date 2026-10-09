import { useEffect, useState } from "react";
import { api } from "@/api/client";

const cache = new Map<string, Promise<string>>();

/**
 * One block in the target language, translated once. The server skips a block that
 * is already in it; a block that cannot be translated stays as it was, so a broken
 * translator never silences the agent.
 */
export function translateBlock(text: string, target: string): Promise<string> {
  const key = `${target}\u0000${text}`;
  let hit = cache.get(key);
  if (!hit) {
    hit = api.translateBlock(text, target).then(
      (r) => r.text,
      () => text,
    );
    cache.set(key, hit);
  }
  return hit;
}

/** `text` as it should be shown: translated when `target` is set, as written until it lands. */
export function useTranslated(text: string, target: string | null): { shown: string; original: string | null } {
  const [done, setDone] = useState<{ key: string; text: string } | null>(null);
  const key = target ? `${target}\u0000${text}` : "";
  useEffect(() => {
    if (!target || !text.trim()) return;
    let live = true;
    void translateBlock(text, target).then((t) => live && setDone({ key, text: t }));
    return () => {
      live = false;
    };
  }, [key, target, text]);
  if (target && done?.key === key && done.text !== text) return { shown: done.text, original: text };
  return { shown: text, original: null };
}
