import type { AgentMessage } from "@/api/types";
import { segments } from "../segments";

export interface SpokenBlock {
  /** Stable while the reply grows: the message and the block's place among its said blocks. */
  key: string;
  text: string;
}

/**
 * Every block of text the agent has said to the owner, in order: what it said and
 * thought while working, then the reply once it has landed. The reply is skipped
 * when it repeats the last thing said, which is how the chat shows it too.
 */
export function blocksOf(messages: AgentMessage[]): SpokenBlock[] {
  const out: SpokenBlock[] = [];
  for (const m of messages) {
    if (m.role !== "agent") continue;
    const running = m.status === "queued" || m.status === "running";
    const landed = !running && !!m.text.trim();
    const said = segments(m.events, landed, m.text).filter((s) => s.said.trim());
    const spoken = new Set<string>();
    said.forEach((s, i) => {
      spoken.add(s.said.trim());
      out.push({ key: `${m.id}:${i}`, text: s.said });
    });
    if (landed && m.status !== "error" && !spoken.has(m.text.trim())) out.push({ key: `${m.id}:reply`, text: m.text });
  }
  return out;
}
