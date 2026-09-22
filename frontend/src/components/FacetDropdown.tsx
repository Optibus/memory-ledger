import { Dropdown } from "./Dropdown";
import { Checkbox } from "./Checkbox";
import "./FacetDropdown.css";

export interface FacetOption {
  value: string;
  label?: string;
  count?: number;
  swatch?: string;
  title?: string;
}

export interface FacetDropdownProps {
  name: string;
  options: FacetOption[];
  /** The values currently switched ON. Absent from here means filtered out. */
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
  /** Rendered under the options — the note explaining what the facet means. */
  note?: React.ReactNode;
}

/**
 * The multi-select facet, with an all/none that applies to this facet only.
 *
 * The closed button says what the menu is doing (`all`, `none`, `4/9`) so a
 * filter can never be silently on behind a collapsed menu — that was the single
 * most confusing thing about the old map.
 */
export function FacetDropdown({ name, options, selected, onChange, note }: FacetDropdownProps) {
  const total = options.length;
  const on = options.filter((o) => selected.has(o.value)).length;
  const summary = on === total ? "all" : on === 0 ? "none" : `${on}/${total}`;

  const setAll = (all: boolean) =>
    onChange(all ? new Set(options.map((o) => o.value)) : new Set());

  const toggle = (value: string, checked: boolean) => {
    const next = new Set(selected);
    if (checked) next.add(value);
    else next.delete(value);
    onChange(next);
  };

  return (
    <Dropdown label={name} summary={summary} active={on !== total}>
      <div className="ml-facet__head">
        <span className="ml-facet__title">{name}</span>
        <button type="button" className="ml-facet__bulk" onClick={() => setAll(true)}>all</button>
        <button type="button" className="ml-facet__bulk" onClick={() => setAll(false)}>none</button>
      </div>
      <div className="ml-facet__opts">
        {options.length === 0 ? (
          <p className="ml-facet__note">nothing in the brain carries a {name.replace(/s$/, "")} yet</p>
        ) : (
          options.map((o) => (
            <Checkbox
              key={o.value}
              checked={selected.has(o.value)}
              onChange={(c) => toggle(o.value, c)}
              label={o.label ?? o.value}
              count={o.count}
              swatch={o.swatch}
              title={o.title}
            />
          ))
        )}
      </div>
      {note ? <div className="ml-facet__note">{note}</div> : null}
    </Dropdown>
  );
}
