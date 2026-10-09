import type { AgentEvent } from "@/api/types";

export type Segment = { said: string; thought?: boolean; events: AgentEvent[] };

/**
 * The reply's events in order: what the agent said on its own (a `text` event, a subagent's too) or thought (a top-level `thinking` event, labelled) as a message, each run of tool calls between those as one
 * folded log. The last thing said, when it is the reply itself, already shown below,
 * is dropped once the reply has landed.
 */
const MARKERS = /[ \t`]*\[\[(?:next:[\s\S]*?|needs-answer)\]\][ \t`]*/g;
const bare = (text: string) => text.replace(MARKERS, "").trim();

export function segments(events: AgentEvent[], landed: boolean, reply: string): Segment[] {
  const said = (e: AgentEvent) => (e.kind === "text" || (e.kind === "thinking" && !e.parent)) && !!(e.output ?? "").trim();
  const isText = (e: AgentEvent) => e.kind === "text";
  let last = -1;
  if (landed) events.forEach((e, i) => (last = said(e) && isText(e) && !e.parent && bare(e.output ?? "") === reply.trim() ? i : last));
  const out: Segment[] = [];
  events.forEach((e, i) => {
    if (i === last) return;
    if (said(e)) out.push({ said: e.output ?? "", thought: !isText(e), events: [] });
    else if (out.length && !out[out.length - 1]!.said) out[out.length - 1]!.events.push(e);
    else out.push({ said: "", events: [e] });
  });
  return out;
}
