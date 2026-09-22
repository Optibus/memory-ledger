import { useState } from "react";
import { Button, Field, Modal, StatusMessage, TextArea, TextInput } from "@/components";

export interface ProcessDraft {
  title: string;
  trigger: string;
  body: string;
  realm: string;
}

export interface ProcessDialogProps {
  /** Where it lands unless the person says otherwise. */
  realm: string;
  busy?: boolean;
  error?: string | null;
  onCancel: () => void;
  onSubmit: (draft: ProcessDraft) => void;
}

/** Record a way of working in a realm. Only the title is required. */
export function ProcessDialog({ realm, busy, error, onCancel, onSubmit }: ProcessDialogProps) {
  const [d, setD] = useState<ProcessDraft>({ title: "", trigger: "", body: "", realm });
  const set = (k: keyof ProcessDraft) => (e: { target: { value: string } }) =>
    setD((prev) => ({ ...prev, [k]: e.target.value }));

  return (
    <Modal
      title="New process"
      subtitle={
        <>
          A way of working, stored as a <code>process</code> node part-of <code>hub_process</code>, in
          one realm.
        </>
      }
      onClose={onCancel}
      width={620}
      footer={
        <>
          <Button
            variant="primary"
            disabled={!d.title.trim() || busy}
            onClick={() =>
              onSubmit({
                title: d.title.trim(),
                trigger: d.trigger.trim(),
                body: d.body,
                realm: d.realm.trim().toLowerCase(),
              })
            }
          >
            Save process
          </Button>
          <Button onClick={onCancel}>Cancel</Button>
        </>
      }
    >
      {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}
      <Field label="Title" required>
        {(id) => <TextInput id={id} value={d.title} autoFocus onChange={set("title")} />}
      </Field>
      <Field label="When it applies" hint="The situation that should make someone reach for it.">
        {(id) => <TextInput id={id} value={d.trigger} onChange={set("trigger")} />}
      </Field>
      <Field label="Steps">
        {(id) => <TextArea id={id} rows={10} value={d.body} onChange={set("body")} />}
      </Field>
      <Field
        label="Realm"
        hint={
          <>
            Defaults to <code>{realm}</code>, the brain&rsquo;s current realm. Lowercase letters, digits
            and dashes.
          </>
        }
      >
        {(id) => <TextInput id={id} value={d.realm} onChange={set("realm")} />}
      </Field>
    </Modal>
  );
}
