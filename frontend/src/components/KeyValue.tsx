import type { ReactNode } from "react";
import "./KeyValue.css";

export interface KeyValueProps {
  /** `null`/`undefined`/`""` rows are dropped, so a blank field shows nothing. */
  pairs: [ReactNode, ReactNode][];
}

export function KeyValue({ pairs }: KeyValueProps) {
  const rows = pairs.filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (!rows.length) return null;
  return (
    <dl className="ml-kv">
      {rows.map(([k, v], i) => (
        <div className="ml-kv__row" key={i}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
