import { useState } from "react";
import { Button, Field, Modal, TextInput } from "@/components";

export interface PromptDialogProps {
  kind: "export-tree" | "ingest-tree";
  onCancel: () => void;
  onSubmit: (dir: string) => void;
}

const COPY = {
  "export-tree": {
    title: "Export the learned tree to markdown",
    sub: (
      <>
        Every learned skill becomes a <code>SKILL.md</code> under the directory, with the rules they
        honor beside them. Re-running overwrites its own files and deletes nothing.
      </>
    ),
    placeholder: "/path/to/learned_skills",
    hint: <>Blank writes to the brain&rsquo;s own <code>learned_skills</code> directory.</>,
    submit: "Export",
  },
  "ingest-tree": {
    title: "Ingest skills from markdown",
    sub: (
      <>
        Every <code>SKILL.md</code> under the directory becomes a skill node. Re-running is safe: a
        skill is refreshed, and a rule this brain already holds is kept, never overwritten by the
        incoming copy.
      </>
    ),
    placeholder: "/path/to/learned_skills",
    hint: (
      <>
        Blank reads both default trees: the plugin&rsquo;s own <code>skills/</code> and the
        brain&rsquo;s <code>learned_skills</code>. A <code>rules/</code> subfolder is ingested first,
        so the skills beside it can link to the rules that travelled with them.
      </>
    ),
    submit: "Ingest",
  },
} as const;

/** Asks for one directory, with the sentence explaining what blank means. */
export function PromptDialog({ kind, onCancel, onSubmit }: PromptDialogProps) {
  const copy = COPY[kind];
  const [value, setValue] = useState("");

  return (
    <Modal
      title={copy.title}
      subtitle={copy.sub}
      onClose={onCancel}
      width={560}
      footer={
        <>
          <Button variant="primary" onClick={() => onSubmit(value.trim())}>{copy.submit}</Button>
          <Button onClick={onCancel}>Cancel</Button>
        </>
      }
    >
      <Field label="Directory" hint={copy.hint}>
        {(id) => (
          <TextInput
            id={id}
            value={value}
            autoFocus
            placeholder={copy.placeholder}
            onChange={(e) => setValue(e.target.value)}
          />
        )}
      </Field>
    </Modal>
  );
}
