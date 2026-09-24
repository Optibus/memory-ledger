import { useState } from "react";
import { Badge, Button, Checkbox, Modal } from "@/components";
import type { ScriptChoice, UploadScriptsResult } from "@/api/types";
import { formatBytes } from "./scriptFormat";
import "./ImportDialog.css";

const MODES: [ScriptChoice, string, string][] = [
  ["keep", "Keep mine", "leave the local file exactly as it is"],
  ["replace", "Replace", "overwrite it; the old bytes are copied to backups/ first"],
];

export interface ScriptImportDialogProps {
  filename: string;
  staged: UploadScriptsResult;
  onCancel: () => void;
  onConfirm: (select: Record<string, ScriptChoice>) => void;
}

/**
 * Choose which scripts land in `<home>/scripts`.
 *
 * A script that differs from the local file of the same name defaults to
 * "keep mine": it is code, and an import that silently overwrote a working copy
 * would be the worst way to find out two machines had drifted. An identical one
 * starts unticked, since there is nothing to bring in.
 */
export function ScriptImportDialog({ filename, staged, onCancel, onConfirm }: ScriptImportDialogProps) {
  const [state, setState] = useState<Record<string, { on: boolean; mode: ScriptChoice }>>(() =>
    Object.fromEntries(
      staged.items.map((i) => [i.name, { on: !i.identical, mode: "keep" as ScriptChoice }]),
    ),
  );
  const clashes = staged.items.filter((i) => i.exists && !i.identical);
  const chosen = staged.items.filter((i) => state[i.name]?.on).length;

  return (
    <Modal
      title={`Import scripts from ${filename}`}
      subtitle={
        <>
          {staged.items.length === 1 ? "1 script" : `${staged.items.length} scripts`}, landing in{" "}
          <code>{staged.dir}</code>.
          {clashes.length
            ? ` ${clashes.length} differ from a local file of the same name — say which to replace. Nothing is written until you confirm.`
            : " None of them would overwrite a different local file."}
        </>
      }
      onClose={onCancel}
      width={680}
      footer={
        <>
          <span className="ml-import__count">
            {chosen} of {staged.items.length} will be imported
          </span>
          <span style={{ flex: 1 }} />
          <Button onClick={onCancel}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!chosen}
            onClick={() =>
              onConfirm(
                Object.fromEntries(
                  Object.entries(state)
                    .filter(([, v]) => v.on)
                    .map(([name, v]) => [name, v.mode]),
                ),
              )
            }
          >
            Import
          </Button>
        </>
      }
    >
      {clashes.length > 1 ? (
        <div className="ml-import__bulk">
          <span>For every script that differs, set them all to:</span>
          {MODES.map(([mode, label]) => (
            <Button
              key={mode}
              size="sm"
              onClick={() =>
                setState((prev) => {
                  const next = { ...prev };
                  for (const i of clashes) next[i.name] = { ...next[i.name]!, mode };
                  return next;
                })
              }
            >
              {label}
            </Button>
          ))}
        </div>
      ) : null}

      {staged.items.map((item) => {
        const st = state[item.name]!;
        const differs = item.exists && !item.identical;
        return (
          <div className={`ml-import__item${st.on ? "" : " is-off"}`} key={item.name}>
            <div className="ml-import__head">
              <Checkbox
                checked={st.on}
                onChange={(on) => setState((p) => ({ ...p, [item.name]: { ...st, on } }))}
                label={
                  <span className="ml-import__t">
                    <b>{item.name}</b>
                    <span className="ml-import__id">
                      {item.language} · {formatBytes(item.bytes)}
                      {item.executable ? " · executable" : ""}
                    </span>
                    {item.summary ? <span className="ml-import__d">{item.summary}</span> : null}
                  </span>
                }
              />
              <Badge tone={differs ? "warn" : "neutral"}>
                {item.identical ? "identical" : differs ? "differs from yours" : "new"}
              </Badge>
            </div>
            {differs && st.on ? (
              <div className="ml-import__modes">
                <span className="ml-import__lbl">
                  Yours is {formatBytes(item.existing_bytes ?? 0)}:
                </span>
                {MODES.map(([mode, label, why]) => (
                  <Button
                    key={mode}
                    size="sm"
                    title={why}
                    active={st.mode === mode}
                    onClick={() => setState((p) => ({ ...p, [item.name]: { ...st, mode } }))}
                  >
                    {label}
                  </Button>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </Modal>
  );
}
