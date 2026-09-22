import { useState } from "react";
import { Badge, Button, Checkbox, Modal, Select } from "@/components";
import type { ImportChoice, UploadBundleResult } from "@/api/types";
import "./ImportDialog.css";

const MODES: [ImportChoice, string, string][] = [
  ["update", "Update", "refresh it from the file, keep the node"],
  ["replace", "Replace", "delete this brain's copy, then create it fresh"],
  ["ignore", "Ignore", "leave this brain's copy exactly as it is"],
];

export interface ImportDialogProps {
  filename: string;
  staged: UploadBundleResult;
  /** The realm processes land in by default — the brain's current one. */
  realm?: string;
  /** Every realm this brain knows, for landing processes somewhere else. */
  realms?: string[];
  onCancel: () => void;
  onConfirm: (select: Record<string, ImportChoice>, processRealm?: string) => void;
}

/**
 * The dialog the person actually decides in.
 *
 * An item already here is the only interesting case, and it gets the three-way
 * answer the question deserves — update it, replace it outright, or leave it
 * alone — rather than one silent policy applied to everything. Nothing is
 * written until confirm: the upload staged the file and read it, no more.
 */
export function ImportDialog({
  filename,
  staged,
  realm = "",
  realms = [],
  onCancel,
  onConfirm,
}: ImportDialogProps) {
  const [target, setTarget] = useState(realm);
  const hasProcesses = staged.items.some((i) => i.kind === "process");
  const [state, setState] = useState<Record<string, { on: boolean; mode: ImportChoice }>>(() =>
    Object.fromEntries(staged.items.map((i) => [i.id, { on: true, mode: "update" as ImportChoice }])),
  );

  const clashes = staged.items.filter((i) => i.exists);
  const chosen = staged.items.filter((i) => state[i.id]?.on).length;

  const setAllExisting = (mode: ImportChoice) =>
    setState((prev) => {
      const next = { ...prev };
      for (const i of clashes) next[i.id] = { ...next[i.id]!, mode };
      return next;
    });

  return (
    <Modal
      title={`Import from ${filename}`}
      subtitle={
        staged.items.length === 1
          ? "1 item in this bundle"
          : `${staged.items.length} items in this bundle` +
            (clashes.length
              ? `, ${clashes.length} of which this brain already holds. Say what should happen to those — nothing is written until you confirm.`
              : ". None of them collides with anything here.")
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
                    .map(([id, v]) => [id, v.mode]),
                ),
                hasProcesses ? target : undefined,
              )
            }
          >
            Import
          </Button>
        </>
      }
    >
      {hasProcesses ? (
        <div className="ml-import__bulk">
          <span>Processes land in the realm:</span>
          <Select
            aria-label="process realm"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            options={[...new Set([realm, ...realms].filter(Boolean))].map((r) => ({
              value: r,
              label: r === realm ? `${r} (current)` : r,
            }))}
          />
          <span className="ml-import__id">the realm each came from is kept as origin_realm</span>
        </div>
      ) : null}

      {clashes.length ? (
        <div className="ml-import__bulk">
          <span>For everything already here, set them all to:</span>
          {MODES.map(([mode, label]) => (
            <Button key={mode} size="sm" onClick={() => setAllExisting(mode)}>
              {label}
            </Button>
          ))}
        </div>
      ) : null}

      {staged.items.map((item) => {
        const st = state[item.id]!;
        return (
          <div className={`ml-import__item${st.on ? "" : " is-off"}`} key={item.id}>
            <div className="ml-import__head">
              <Checkbox
                checked={st.on}
                onChange={(on) => setState((p) => ({ ...p, [item.id]: { ...st, on } }))}
                label={
                  <span className="ml-import__t">
                    <b>{item.title || item.id}</b>
                    <span className="ml-import__id">{item.id}</span>
                    {item.description ? (
                      <span className="ml-import__d">{item.description}</span>
                    ) : null}
                  </span>
                }
              />
              <Badge tone={item.exists ? "warn" : "neutral"}>
                {item.exists ? "already here" : item.kind}
              </Badge>
              {item.kind === "process" && item.realm ? <Badge>{`from ${item.realm}`}</Badge> : null}
            </div>
            {item.exists && st.on ? (
              <div className="ml-import__modes">
                <span className="ml-import__lbl">This one exists:</span>
                {MODES.map(([mode, label, why]) => (
                  <Button
                    key={mode}
                    size="sm"
                    title={why}
                    active={st.mode === mode}
                    onClick={() => setState((p) => ({ ...p, [item.id]: { ...st, mode } }))}
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
