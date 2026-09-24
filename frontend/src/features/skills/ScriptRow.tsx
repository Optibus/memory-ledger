import { useState } from "react";
import { api } from "@/api/client";
import { Badge, Checkbox, StatusMessage } from "@/components";
import type { ScriptEntry } from "@/api/types";
import { formatBytes } from "./scriptFormat";
import { Highlight } from "./Highlight";
import "./RuleRow.css";

export interface ScriptRowProps {
  script: ScriptEntry;
  query?: string;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (on: boolean) => void;
}

/** A local script you can open and read. Reading is all the page does with one: it never runs it. */
export function ScriptRow({ script, query = "", selectable, selected = false, onSelect }: ScriptRowProps) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || text !== null) return;
    try {
      const r = await api.script(script.name);
      setText(r.script?.text ?? "");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="ml-rulerow">
      <div className="ml-rulerow__head">
        {selectable ? (
          <Checkbox
            checked={selected}
            onChange={(on) => onSelect?.(on)}
            label={<span className="sr-only">select {script.name}</span>}
          />
        ) : null}
        <button type="button" className="ml-rulerow__btn" aria-expanded={open} onClick={toggle}>
          <Badge>{script.language}</Badge>
          <span className="ml-rulerow__title">
            <Highlight text={script.name} query={query} />
            <span className="ml-rulerow__id">
              {script.summary ? <Highlight text={script.summary} query={query} /> : "no description line"}
              {" · "}
              {formatBytes(script.bytes)}
              {script.executable ? " · executable" : ""}
            </span>
          </span>
        </button>
      </div>
      {open ? (
        error ? (
          <StatusMessage tone="error">{error}</StatusMessage>
        ) : (
          <pre className="ml-rulerow__body ml-rulerow__body--code">{text ?? "loading…"}</pre>
        )
      ) : null}
    </div>
  );
}
