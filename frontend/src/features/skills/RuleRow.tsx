import { useState } from "react";
import { api } from "@/api/client";
import { Badge, Checkbox, StatusMessage } from "@/components";
import { Highlight } from "./Highlight";
import type { RuleWhy } from "@/api/types";
import "./RuleRow.css";

/** The three reasons a rule sits on a card are genuinely different questions. */
export const WHY_LABEL: Record<RuleWhy, [string, string]> = {
  declared: ["declared", "the skill names this rule itself"],
  matched: ["matched", "match-rules returns it for this skill's triggers"],
  always: ["always on", "non-negotiable, applies whether or not it matched"],
};

export interface RuleRowProps {
  id: string;
  title: string;
  /** The `why` badge on a skill card; a source badge in the rule catalogue. */
  why?: RuleWhy;
  source?: string;
  nonNegotiable?: boolean;
  query?: string;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (on: boolean) => void;
}

/** A rule you can open and read in full, wherever it is listed. */
export function RuleRow({
  id,
  title,
  why,
  source,
  nonNegotiable,
  query = "",
  selectable,
  selected = false,
  onSelect,
}: RuleRowProps) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || body !== null) return;
    try {
      const r = await api.rule(id);
      setBody(r.rule?.body || r.rule?.rule || "(this rule has no body)");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const label = why ? WHY_LABEL[why] : null;

  return (
    <div className="ml-rulerow">
      <div className="ml-rulerow__head">
        {selectable ? (
          <Checkbox checked={selected} onChange={(on) => onSelect?.(on)} label={<span className="sr-only">select {title}</span>} />
        ) : null}
        <button
          type="button"
          className="ml-rulerow__btn"
          aria-expanded={open}
          title={label?.[1]}
          onClick={toggle}
        >
          <Badge tone={nonNegotiable ? "danger" : "neutral"}>
            {label ? label[0] : source || "rule"}
          </Badge>
          <span className="ml-rulerow__title">
            <Highlight text={title} query={query} />
            <span className="ml-rulerow__id">{id}</span>
          </span>
        </button>
      </div>
      {open ? (
        error ? (
          <StatusMessage tone="error">{error}</StatusMessage>
        ) : (
          <pre className="ml-rulerow__body">{body ?? "loading…"}</pre>
        )
      ) : null}
    </div>
  );
}
