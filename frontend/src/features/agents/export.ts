import type { AgentConversation, AgentMessage } from "@/api/types";

/**
 * A conversation as a file: JSON (everything, events included), plain text, or
 * a print view the browser saves as PDF. Kept apart from the section so the
 * formats can be tested without a DOM download.
 */

export type ExportFormat = "json" | "txt" | "pdf";

export function formatCost(usd: number | null | undefined, estimated = false, local = false): string {
  if (local && !usd) return "$0 · local";
  if (usd === null || usd === undefined) return "—";
  const v = usd < 0.01 && usd > 0 ? usd.toFixed(4) : usd.toFixed(2);
  return `${estimated ? "≈ " : ""}$${v}`;
}

function who(m: AgentMessage): string {
  return m.role === "user" ? "You" : `${m.model || "agent"}${m.connector ? ` (${m.connector})` : ""}`;
}

export function toJson(conversation: AgentConversation, messages: AgentMessage[]): string {
  return JSON.stringify({ conversation, messages, exported_at: new Date().toISOString() }, null, 2);
}

export function toText(conversation: AgentConversation, messages: AgentMessage[]): string {
  const lines = [`# ${conversation.title || "Untitled conversation"}`, `exported ${new Date().toISOString()}`, ""];
  for (const m of messages) {
    const status = m.role === "agent" && m.status !== "done" ? ` [${m.status}]` : "";
    lines.push(`[${m.at}] ${who(m)}${status}:`);
    if (m.role === "agent" && m.events.length) {
      for (const e of m.events) {
        if (e.kind === "thinking" || e.kind === "text") continue;
        lines.push(`  · ${e.kind} ${e.name}${e.error ? " (failed)" : ""}`);
      }
    }
    lines.push(m.text || m.error || "", "");
  }
  return lines.join("\n");
}

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function toPrintHtml(conversation: AgentConversation, messages: AgentMessage[]): string {
  const rows = messages
    .map((m) => {
      const tools = m.events.filter((e) => e.kind !== "thinking" && e.kind !== "text");
      return `<div class="m ${m.role}"><div class="h">${esc(who(m))} · ${esc(m.at)}</div>${
        tools.length ? `<div class="t">${tools.map((e) => esc(`${e.kind}: ${e.name}`)).join(" · ")}</div>` : ""
      }<div class="b">${esc(m.text || m.error || "")}</div></div>`;
    })
    .join("");
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(conversation.title)}</title><style>
body{font:13px/1.5 system-ui,sans-serif;margin:32px;color:#111}
h1{font-size:18px}.m{margin:12px 0;padding:8px 12px;border-radius:8px;border:1px solid #ddd;max-width:80%}
.user{margin-left:auto;background:#eef4ff}.h{color:#666;font-size:11px}.t{color:#666;font-size:11px;font-style:italic}
.b{white-space:pre-wrap}</style></head><body><h1>${esc(conversation.title)}</h1>${rows}</body></html>`;
}

export function fileName(conversation: AgentConversation, ext: string): string {
  const slug = (conversation.title || "conversation").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return `${slug.slice(0, 50) || "conversation"}.${ext}`;
}

/** Hand the file to the browser. PDF opens the print dialog on a clean page. */
export function exportConversation(format: ExportFormat, conversation: AgentConversation, messages: AgentMessage[]) {
  if (format === "pdf") {
    const w = window.open("", "_blank");
    if (!w) return false;
    w.document.write(toPrintHtml(conversation, messages));
    w.document.close();
    w.focus();
    w.print();
    return true;
  }
  const [body, type] =
    format === "json"
      ? [toJson(conversation, messages), "application/json"]
      : [toText(conversation, messages), "text/plain"];
  const url = URL.createObjectURL(new Blob([body], { type: `${type};charset=utf-8` }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName(conversation, format);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return true;
}
