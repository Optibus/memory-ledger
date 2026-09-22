import type { HTMLAttributes, ReactNode } from "react";
import "./Panel.css";

export interface PanelProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  /** Drops the blur/shadow for panels that sit inside another surface. */
  flat?: boolean;
}

/** The frosted surface every floating box in the map section is made of. */
export function Panel({ children, flat = false, className = "", ...rest }: PanelProps) {
  return (
    <div className={["ml-panel", flat ? "ml-panel--flat" : "", className].filter(Boolean).join(" ")} {...rest}>
      {children}
    </div>
  );
}
