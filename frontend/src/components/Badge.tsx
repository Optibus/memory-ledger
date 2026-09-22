import type { ReactNode } from "react";
import "./Badge.css";

export type BadgeTone = "neutral" | "accent" | "success" | "warn" | "danger" | "type";

export interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone;
  /** An explicit colour, for node-type badges painted from the type palette. */
  color?: string;
  title?: string;
}

/** A small status word: a node type, a state, a source, a count. */
export function Badge({ children, tone = "neutral", color, title }: BadgeProps) {
  const style = color ? { background: color, color: "#000", fontWeight: 600 } : undefined;
  return (
    <span className={`ml-badge ml-badge--${tone}`} style={style} title={title}>
      {children}
    </span>
  );
}
