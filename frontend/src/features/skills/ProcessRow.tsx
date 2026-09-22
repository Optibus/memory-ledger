import { useState } from "react";
import { Badge, Checkbox } from "@/components";
import { Highlight } from "./Highlight";
import type { ProcessNode } from "@/api/types";
import "./RuleRow.css";
import "./ProcessRow.css";

export interface ProcessRowProps {
  process: ProcessNode;
  /** The realm new processes land in; a process from any other is labelled as such. */
  currentRealm: string;
  query?: string;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (on: boolean) => void;
}

/**
 * One way of working, opened in place.
 *
 * The body is already in the payload — processes are short and few — so opening
 * one costs no request, unlike a rule.
 */
export function ProcessRow({
  process: p,
  currentRealm,
  query = "",
  selectable,
  selected = false,
  onSelect,
}: ProcessRowProps) {
  const [open, setOpen] = useState(false);
  const elsewhere = p.realm !== currentRealm;

  return (
    <div className="ml-rulerow">
      <div className="ml-rulerow__head">
        {selectable ? (
          <Checkbox
            checked={selected}
            onChange={(on) => onSelect?.(on)}
            label={<span className="sr-only">select {p.title}</span>}
          />
        ) : null}
        <button
          type="button"
          className="ml-rulerow__btn"
          aria-expanded={open}
          title={elsewhere ? `from the ${p.realm} realm` : `in ${p.realm}, the current realm`}
          onClick={() => setOpen((v) => !v)}
        >
          <Badge tone={elsewhere ? "warn" : "neutral"}>{p.realm || "no realm"}</Badge>
          <span className="ml-rulerow__title">
            <Highlight text={p.title} query={query} />
            {p.trigger ? (
              <span className="ml-process__trigger">
                when: <Highlight text={p.trigger} query={query} />
              </span>
            ) : null}
            <span className="ml-rulerow__id">{p.id}</span>
          </span>
        </button>
      </div>
      {open ? (
        <div className="ml-process__open">
          <pre className="ml-rulerow__body">{p.body || "(this process has no body)"}</pre>
          <p className="ml-process__meta">
            {[
              p.established ? `established ${p.established}` : null,
              p.updated ? `updated ${p.updated.slice(0, 10)}` : null,
              `${p.edges} edge${p.edges === 1 ? "" : "s"}`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
      ) : null}
    </div>
  );
}
