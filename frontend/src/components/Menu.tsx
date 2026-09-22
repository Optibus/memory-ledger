import type { ReactNode } from "react";
import { Dropdown } from "./Dropdown";
import "./Menu.css";

export interface MenuItem {
  id: string;
  label: ReactNode;
  description?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

export interface MenuGroup {
  heading?: string;
  items: MenuItem[];
}

export interface MenuProps {
  label: string;
  groups: MenuGroup[];
  align?: "left" | "right";
}

/**
 * Everything that ACTS on a corpus, behind one button, so the surface
 * underneath stays a reading surface.
 */
export function Menu({ label, groups, align = "right" }: MenuProps) {
  return (
    <Dropdown label={label} align={align} className="ml-menu">
      {groups.map((g, gi) => (
        <div key={gi} className="ml-menu__group">
          {gi > 0 ? <hr className="ml-menu__sep" /> : null}
          {g.heading ? <div className="ml-menu__heading">{g.heading}</div> : null}
          {g.items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`ml-menu__item${item.danger ? " is-danger" : ""}`}
              disabled={item.disabled}
              onClick={item.onSelect}
            >
              <b>{item.label}</b>
              {item.description ? <span className="ml-menu__desc">{item.description}</span> : null}
            </button>
          ))}
        </div>
      ))}
    </Dropdown>
  );
}
