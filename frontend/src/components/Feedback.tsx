import type { ReactNode } from "react";
import "./Feedback.css";

export interface StatusMessageProps {
  tone?: "info" | "error" | "success";
  children: ReactNode;
}

/** What the last action did. Renders nothing when there is nothing to say. */
export function StatusMessage({ tone = "info", children }: StatusMessageProps) {
  if (!children) return null;
  return (
    <div className={`ml-status ml-status--${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export interface EmptyStateProps {
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
}

/** Says why a list is empty, which is never the same as showing nothing. */
export function EmptyState({ title, hint, action }: EmptyStateProps) {
  return (
    <div className="ml-empty">
      <p className="ml-empty__title">{title}</p>
      {hint ? <p className="ml-empty__hint">{hint}</p> : null}
      {action}
    </div>
  );
}

export function Spinner({ label = "loading" }: { label?: string }) {
  return (
    <span className="ml-spinner" role="status" aria-live="polite">
      <span className="ml-spinner__dot" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export interface LiveDotProps {
  ok: boolean;
  label: string;
}

/** The pulse that says the page is still talking to the server. */
export function LiveDot({ ok, label }: LiveDotProps) {
  return (
    <span className="ml-live" title={label}>
      <span className={`ml-live__dot${ok ? "" : " is-down"}`} aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}
