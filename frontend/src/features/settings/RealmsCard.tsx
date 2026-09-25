import { useState } from "react";
import { api } from "@/api/client";
import { Badge, Button, Card, ConfirmDialog, Field, Select, StatusMessage, TextInput } from "@/components";
import type { RealmCount } from "@/api/types";
import "./SettingsSection.css";

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * Realms are open vocabulary (§2d): nothing has a fixed list, and a fresh brain
 * starts with one called `default`. This card is the two things that vocabulary
 * needs and the rest of the page cannot do:
 *
 *   rename  moves every node in a realm to a new name — merging when the new
 *           one already exists — and follows it in the default realm setting.
 *           It rewrites thousands of rows, so it asks first and the engine
 *           snapshots the graph before the first write.
 *   add     a realm with no nodes yet exists only as the setting, so adding one
 *           means making it where new nodes land. The first write creates it.
 */
export function RealmsCard({
  realms,
  current,
  writable,
  onChanged,
}: {
  realms: Record<string, RealmCount>;
  current: string;
  writable: boolean;
  onChanged: () => void;
}) {
  const names = Array.from(new Set([...Object.keys(realms), current].filter(Boolean))).sort();
  const [from, setFrom] = useState(current);
  const [to, setTo] = useState("");
  const [added, setAdded] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ text: string; tone: "info" | "error" | "success" } | null>(null);

  const src = names.includes(from) ? from : current;
  const target = to.trim().toLowerCase();
  const newName = added.trim().toLowerCase();
  const merging = target !== "" && target !== src && names.includes(target);

  const rename = async () => {
    setBusy(true);
    setOut(null);
    try {
      const res = await api.renameRealm(src, target);
      if (!res.ok) throw new Error(res.error || "rename failed");
      setOut({
        text:
          `Moved ${res.moved} node(s) from ${res.from} to ${res.to}` +
          (res.merged_into_existing ? ", merged into the existing realm." : ".") +
          (res.warning ? ` ${res.warning}` : ""),
        tone: res.warning ? "info" : "success",
      });
      setFrom(res.to);
      setTo("");
      onChanged();
    } catch (e) {
      setOut({ text: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const add = async () => {
    setBusy(true);
    setOut(null);
    try {
      await api.saveSettings({ realm: newName });
      setOut({ text: `New nodes now land in ${newName}.`, tone: "success" });
      setAdded("");
      onChanged();
    } catch (e) {
      setOut({ text: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h2 className="ml-settings__h">Realms</h2>
      <p className="ml-settings__hint">
        Sections of life, each node in exactly one. Names are yours: rename any of them, or add one
        and new nodes start landing there.
      </p>

      <div className="ml-settings__grid">
        {names.map((r) => (
          <span key={r}>
            <Badge>{r}</Badge> {realms[r]?.nodes ?? 0} node(s){r === current ? " · default" : ""}
          </span>
        ))}
      </div>

      <div className="ml-settings__grid">
        <Field label="Rename realm">
          {(id) => (
            <Select
              id={id}
              value={src}
              onChange={(e) => setFrom(e.target.value)}
              options={names.map((r) => ({ value: r, label: r }))}
            />
          )}
        </Field>
        <Field
          label="New name"
          hint={merging ? `${target} already exists: its nodes and ${src}'s become one realm.` : undefined}
        >
          {(id) => (
            <TextInput id={id} value={to} placeholder="work" onChange={(e) => setTo(e.target.value)} />
          )}
        </Field>
      </div>
      <Button
        onClick={() => setConfirming(true)}
        disabled={!writable || busy || !SLUG.test(target) || target === src}
      >
        Rename
      </Button>
      {target && !SLUG.test(target) ? (
        <StatusMessage tone="error">Lowercase letters, digits and dashes only.</StatusMessage>
      ) : null}

      <div className="ml-settings__grid">
        <Field label="Add a realm" hint="Becomes the default realm; it appears in the list once a node is in it.">
          {(id) => (
            <TextInput id={id} value={added} placeholder="personal" onChange={(e) => setAdded(e.target.value)} />
          )}
        </Field>
      </div>
      <Button
        onClick={add}
        disabled={!writable || busy || !SLUG.test(newName) || newName === current}
      >
        Add and make default
      </Button>

      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}

      {confirming ? (
        <ConfirmDialog
          title={`Rename ${src} to ${target}?`}
          body={
            <p>
              Moves {realms[src]?.nodes ?? 0} node(s){merging ? ` into the existing ${target} realm` : ""}.
              The graph is snapshotted first.
            </p>
          }
          confirmLabel="Rename"
          busy={busy}
          onConfirm={rename}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </Card>
  );
}
