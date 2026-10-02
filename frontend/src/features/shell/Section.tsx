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
  /** No visible heading or lead: the title stays, for screen readers only (a chat that wants every line). */
  bare?: boolean;
}

/** The reading layout every non-map section uses, so they line up with each other. */
export function Section({ title, lead, actions, children, width = 860, fill = false, bare = false }: SectionProps) {
  const cls = `ml-section${fill ? " ml-section--fill" : ""}${bare ? " ml-section--bare" : ""}`;
  return (
    <div className={cls} style={fill ? undefined : { maxWidth: width }}>
      {bare ? (
        <h1 className="sr-only">{title}</h1>
      ) : (
        <div className="ml-section__bar">
          <h1 className="ml-section__title">{title}</h1>
          {actions ? <div className="ml-section__actions">{actions}</div> : null}
        </div>
      )}
      {lead && !bare ? <p className="ml-section__lead">{lead}</p> : null}
      {children}
    </div>
  );
}
