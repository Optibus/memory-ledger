import { useState } from "react";
import { api } from "@/api/client";
import { useMeta } from "@/api/MetaContext";
import { Button, Field, Modal, Select, StatusMessage, TextArea, TextInput } from "@/components";

export interface IdeaDialogProps {
  onCancel: () => void;
  /** The new idea's id, so the map can select it once the next read carries it. */
  onCreated: (id: string) => void;
}

/**
 * Record an alternative from the map. Only the title is required.
 *
 * It is the same write `graph.py idea-propose` makes: an `idea` node, status
 * `proposed`, with its first transition logged (CONVENTIONS §2k).
 */
export function IdeaDialog({ onCancel, onCreated }: IdeaDialogProps) {
  const { meta } = useMeta();
  const current = meta?.default_realm ?? "";
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [realm, setRealm] = useState(current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The realms this brain holds, read from /api/meta like every other realm
  // picker. The current one leads.
  const realms = [
    ...new Set([current, ...Object.keys(meta?.realms ?? {}).sort()].filter(Boolean)),
  ];

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.proposeIdea({
        title: title.trim(),
        detail: detail.trim(),
        realm: realm.trim().toLowerCase(),
      });
      onCreated(res.node.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="New idea"
      subtitle={
        <>
          An alternative worth keeping, stored as an <code>idea</code> node, <code>proposed</code>.
          Record every one, not just the one you pick.
        </>
      }
      onClose={onCancel}
      width={560}
      footer={
        <>
          <Button variant="primary" disabled={!title.trim() || busy} onClick={() => void submit()}>
            {busy ? "saving…" : "Save idea"}
          </Button>
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        </>
      }
    >
      {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}
      <Field label="Title" required>
        {(id) => (
          <TextInput id={id} value={title} autoFocus onChange={(e) => setTitle(e.target.value)} />
        )}
      </Field>
      <Field label="Detail" hint="What it is, and why it came up.">
        {(id) => (
          <TextArea id={id} rows={6} value={detail} onChange={(e) => setDetail(e.target.value)} />
        )}
      </Field>
      {realms.length ? (
        <Field
          label="Realm"
          hint={
            <>
              Defaults to <code>{current}</code>, the brain&rsquo;s current realm.
            </>
          }
        >
          {(id) => (
            <Select
              id={id}
              value={realm}
              onChange={(e) => setRealm(e.target.value)}
              options={realms.map((r) => ({ value: r, label: r === current ? `${r} (current)` : r }))}
            />
          )}
        </Field>
      ) : null}
    </Modal>
  );
}
