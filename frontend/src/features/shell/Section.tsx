import type { ReactNode } from "react";
import "./Section.css";

export interface SectionProps {
  title: string;
  lead?: ReactNode;
  /** Sits on the heading row, on the right — an options menu, usually. */
  actions?: ReactNode;
  children: ReactNode;
  /** Wider sections (settings, skills) share one measure. */
  width?: number;
  /** Take the whole viewport, edge to edge, and let the child own the scroll (a chat). */
  fill?: boolean;
}

/** The reading layout every non-map section uses, so they line up with each other. */
export function Section({ title, lead, actions, children, width = 860, fill = false }: SectionProps) {
  return (
    <div className={`ml-section${fill ? " ml-section--fill" : ""}`} style={fill ? undefined : { maxWidth: width }}>
      <div className="ml-section__bar">
        <h1 className="ml-section__title">{title}</h1>
        {actions ? <div className="ml-section__actions">{actions}</div> : null}
      </div>
      {lead ? <p className="ml-section__lead">{lead}</p> : null}
      {children}
    </div>
  );
}
