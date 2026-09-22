import type { ReactNode } from "react";
import "./Checkbox.css";

export interface CheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  /** The tally on the right — how many nodes carry this value. */
  count?: number | string;
  /** A colour dot in front of the label, for facets that paint the map. */
  swatch?: string;
  title?: string;
  disabled?: boolean;
}

/** One row in a facet menu: a box, a label, and what it would cost you. */
export function Checkbox({ checked, onChange, label, count, swatch, title, disabled }: CheckboxProps) {
  return (
    <label className="ml-check" title={title}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {swatch ? <span className="ml-check__swatch" style={{ background: swatch }} aria-hidden="true" /> : null}
      <span className="ml-check__label">{label}</span>
      {count !== undefined ? <span className="ml-check__count">{count}</span> : null}
    </label>
  );
}
