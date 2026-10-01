import { useMemo } from "react";
import type { ReactNode } from "react";
import { parseRich } from "@/lib/richText";
import type { RichNode } from "@/lib/richText";
import "./RichText.css";

/**
 * A message with its formatting: bold, italic, underline, strike, code, and
 * links that open in a new tab. Built from React elements, never from HTML, so
 * whatever a message says, it can only ever be text and links (see
 * `lib/richText`).
 */
export function RichText({ text, className }: { text: string; className?: string }) {
  const nodes = useMemo(() => parseRich(text), [text]);
  return <span className={`ml-rich${className ? ` ${className}` : ""}`}>{render(nodes)}</span>;
}

function render(nodes: RichNode[]): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text":
        return n.v;
      case "code":
        return (
          <code key={i} className="ml-rich__code">
            {n.v}
          </code>
        );
      case "pre":
        return (
          <pre key={i} className="ml-rich__pre">
            <code>{n.v}</code>
          </pre>
        );
      case "link":
        return (
          <a key={i} href={n.href} target="_blank" rel="noopener noreferrer" className="ml-rich__link">
            {render(n.c)}
          </a>
        );
      case "b":
        return <strong key={i}>{render(n.c)}</strong>;
      case "i":
        return <em key={i}>{render(n.c)}</em>;
      case "u":
        return <u key={i}>{render(n.c)}</u>;
      case "s":
        return <s key={i}>{render(n.c)}</s>;
    }
  });
}
