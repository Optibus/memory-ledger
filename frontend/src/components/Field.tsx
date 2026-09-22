import { useId } from "react";
import type { ReactNode } from "react";
import "./Field.css";

export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  required?: boolean;
  error?: string;
  /** Receives the generated id so the label actually points at the control. */
  children: (id: string) => ReactNode;
}

/** A label, its control, and the sentence explaining what the value means. */
export function Field({ label, hint, required, error, children }: FieldProps) {
  const id = useId();
  return (
    <div className={`ml-field${error ? " has-error" : ""}`}>
      <label className="ml-field__label" htmlFor={id}>
        {label}
        {required ? <span className="ml-field__req" title="required"> *</span> : null}
      </label>
      {children(id)}
      {hint ? <p className="ml-field__hint">{hint}</p> : null}
      {error ? <p className="ml-field__error" role="alert">{error}</p> : null}
    </div>
  );
}
