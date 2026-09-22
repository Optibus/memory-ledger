import { Dropdown, FacetDropdown, LiveDot, Panel, SearchInput, Checkbox } from "@/components";
import type { FacetOption } from "@/components";
import type { FilterState } from "@/lib/graphFilter";
import "./MapToolbar.css";

export interface MapToolbarProps {
  query: string;
  onQuery: (q: string) => void;
  filter: FilterState;
  onFilter: (next: FilterState) => void;
  typeOptions: FacetOption[];
  realmOptions: FacetOption[];
  kindOptions: FacetOption[];
  /** How many extra nodes "drop unsupported" took, so the toggle is legible. */
  cascadeCount: number;
  stats: string;
  live: boolean;
}

/** Search, the three facets, the display toggles, and what the map is showing. */
export function MapToolbar({
  query,
  onQuery,
  filter,
  onFilter,
  typeOptions,
  realmOptions,
  kindOptions,
  cascadeCount,
  stats,
  live,
}: MapToolbarProps) {
  // A facet holds what is OFF; the menus speak in what is ON, so an option the
  // brain grows tomorrow is visible by default rather than silently filtered.
  const selection = (options: FacetOption[], off: Set<string>) =>
    new Set(options.map((o) => o.value).filter((v) => !off.has(v)));

  const setOff = (key: "typesOff" | "realmsOff" | "kindsOff", options: FacetOption[]) =>
    (on: Set<string>) =>
      onFilter({
        ...filter,
        [key]: new Set(options.map((o) => o.value).filter((v) => !on.has(v))),
      });

  return (
    <Panel className="ml-toolbar">
      <SearchInput
        value={query}
        onChange={onQuery}
        placeholder="search nodes… (title, type, id)"
      />
      <div className="ml-toolbar__facets">
        <FacetDropdown
          name="types"
          options={typeOptions}
          selected={selection(typeOptions, filter.typesOff)}
          onChange={setOff("typesOff", typeOptions)}
        />
        <FacetDropdown
          name="realms"
          options={realmOptions}
          selected={selection(realmOptions, filter.realmsOff)}
          onChange={setOff("realmsOff", realmOptions)}
        />
        <FacetDropdown
          name="kinds"
          options={kindOptions}
          selected={selection(kindOptions, filter.kindsOff)}
          onChange={setOff("kindsOff", kindOptions)}
          note={
            <>
              Most nodes carry no <code>kind</code> at all; those land in an explicit
              bucket rather than being hidden.
            </>
          }
        />
        <Dropdown label="Display" summary={displaySummary(filter)} active={displayActive(filter)}>
          <Checkbox
            checked={filter.hideDone}
            onChange={(v) => onFilter({ ...filter, hideDone: v })}
            label="hide done / archived"
          />
          <Checkbox
            checked={filter.cascade}
            onChange={(v) => onFilter({ ...filter, cascade: v })}
            label="drop unsupported"
            count={cascadeCount ? `+${cascadeCount}` : undefined}
            title="A node stays only while at least one thing still points at it. Hide every source and it goes too."
          />
          <Checkbox
            checked={filter.shade}
            onChange={(v) => onFilter({ ...filter, shade: v })}
            label="shade instead of hide"
            title="Filtered-out nodes stay on the map, dimmed, instead of leaving it — so the graph keeps its shape and what you kept can be read in place. A search hit stays lit either way."
          />
        </Dropdown>
      </div>
      <span className="ml-toolbar__stats">{stats}</span>
      <LiveDot ok={live} label={live ? "live" : "offline"} />
    </Panel>
  );
}

function displayActive(f: FilterState): boolean {
  return f.hideDone || f.cascade || f.shade;
}

function displaySummary(f: FilterState): string {
  const on = [f.hideDone && "done hidden", f.cascade && "cascade", f.shade && "shade"].filter(
    Boolean,
  ) as string[];
  return on.length ? on.join(" · ") : "default";
}
