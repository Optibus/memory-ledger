import { NavLink } from "react-router-dom";
import "./Sidebar.css";

export interface SectionLink {
  to: string;
  label: string;
  icon: string;
  /** Pinned to the bottom, under the divider. */
  foot?: boolean;
}

export const SECTIONS: SectionLink[] = [
  { to: "/map", label: "Map", icon: "🗺" },
  { to: "/dashboard", label: "Dashboard", icon: "📋" },
  { to: "/people", label: "People", icon: "👥" },
  { to: "/skills", label: "Skills", icon: "🧩" },
  { to: "/settings", label: "Settings", icon: "⚙", foot: true },
];

export interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

/**
 * The section switcher, as a drawer rather than a top bar.
 *
 * A drawer keeps the sections out of the search bar's way and leaves room for
 * Settings pinned at the bottom.
 */
export function Sidebar({ open, onClose }: SidebarProps) {
  const main = SECTIONS.filter((s) => !s.foot);
  const foot = SECTIONS.filter((s) => s.foot);

  const item = (s: SectionLink) => (
    <NavLink
      key={s.to}
      to={s.to}
      className={({ isActive }) => `ml-side__link${isActive ? " is-active" : ""}`}
      onClick={onClose}
    >
      <span aria-hidden="true">{s.icon}</span>
      <span>{s.label}</span>
    </NavLink>
  );

  return (
    <>
      {open ? <div className="ml-side__scrim" onClick={onClose} /> : null}
      <aside className={`ml-side${open ? " is-open" : ""}`} aria-label="sections" aria-hidden={!open}>
        <div className="ml-side__head">
          <span>🧠 second-<b>brain</b></span>
          <button type="button" className="ml-side__x" onClick={onClose} aria-label="close sections">✕</button>
        </div>
        <nav className="ml-side__nav">{main.map(item)}</nav>
        <div className="ml-side__foot">
          {/*
            Not a NavLink: /api/docs is the Swagger UI the SERVER renders, not a
            route this app owns, so it has to be a real navigation. It opens in
            its own tab because reading the API is something you do BESIDE the
            brain, not instead of it — and because coming "back" from a page
            React never rendered would drop whatever you had open here.
          */}
          <a
            className="ml-side__link ml-side__link--out"
            href="./api/docs"
            target="_blank"
            rel="noreferrer"
            onClick={onClose}
          >
            <span aria-hidden="true">📖</span>
            <span>API docs</span>
            <span className="ml-side__ext" aria-hidden="true">↗</span>
          </a>
          {foot.map(item)}
        </div>
      </aside>
    </>
  );
}
