import { useMemo, useState } from "react";
import { api } from "@/api/client";
import { useMeta } from "@/api/MetaContext";
import { useAsync } from "@/hooks/useAsync";
import { Button, Field, Select, StatusMessage, TextArea, TextInput } from "@/components";
import { isCompletable } from "@/lib/vocab";
import { toDateInput } from "@/lib/format";
import type { GraphNode, Person } from "@/api/types";
import "./NodeEditor.css";

export interface NodeEditorProps {
  node: GraphNode;
  onDone: () => void;
  onCancel: () => void;
}

const UNRECOGNISED = "__stale__";

/**
 * Edit a node in place in its card.
 *
 * Title, body and realm are meaningful on ANY node. Due, priority and assignee
 * are only meaningful on something someone has to DO — a person has no due
 * date, and a priority on one would just be a stray prop nothing reads.
 */
export function NodeEditor({ node, onDone, onCancel }: NodeEditorProps) {
  const { meta } = useMeta();
  const props = (node.props ?? {}) as Record<string, string>;
  const schedulable = isCompletable(node.type);

  const [title, setTitle] = useState(node.title || "");
  const [body, setBody] = useState(node.body || "");
  const [realm, setRealm] = useState(node.realm || meta?.default_realm || "");
  const [priority, setPriority] = useState(props.priority || "");
  const [due, setDue] = useState(toDateInput(node.due));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const people = useAsync<Person[]>(
    async (signal) => (await api.people(signal)).people,
    [],
  );
  // A bare `?? []` is a new array each render, which would defeat the memo below.
  const roster = useMemo(() => people.data ?? [], [people.data]);

  const currentAssignee = (props.assignee || "").trim().toLowerCase();
  const matchedPerson = useMemo(
    () =>
      roster.find((p) =>
        [p.title, ...(p.aliases || [])].map((s) => String(s).toLowerCase()).includes(currentAssignee),
      ),
    [roster, currentAssignee],
  );
  const [assignee, setAssignee] = useState<string | null>(null);
  // An assignee string that matches nobody is real data — offer it rather than
  // silently rendering the task as unassigned.
  const assigneeValue =
    assignee ?? (matchedPerson ? matchedPerson.id : currentAssignee ? UNRECOGNISED : "");

  // Realms are OPEN vocabulary read from the brain; the node's current realm is
  // included even when the server has never heard of it, so editing never
  // silently moves the node somewhere else.
  const realms = useMemo(() => {
    const names = new Set<string>();
    for (const r of Object.keys(meta?.realms ?? {})) names.add(r);
    if (node.realm) names.add(node.realm);
    if (meta?.default_realm) names.add(meta.default_realm);
    return [...names].filter(Boolean).sort();
  }, [meta, node.realm]);

  const save = async () => {
    if (!title.trim()) {
      setError("title cannot be empty");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        id: node.id,
        title: title.trim(),
        body,
        realm,
      };
      if (schedulable) {
        payload.priority = priority;
        payload.due = due;
      }
      await api.updateNode(payload);
      // Assignee is a separate write because it rewires an edge as well as a
      // prop; UNRECOGNISED means "leave the unrecognised string alone".
      if (assigneeValue !== UNRECOGNISED && (matchedPerson?.id ?? "") !== assigneeValue) {
        await api.assign({ id: node.id, person_id: assigneeValue });
      }
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(false);
    }
  };

  return (
    <div className="ml-nodeedit">
      <Field label="title" required>
        {(id) => (
          <TextInput id={id} value={title} autoFocus onChange={(e) => setTitle(e.target.value)} />
        )}
      </Field>

      <div className="ml-nodeedit__grid">
        {schedulable ? (
          <>
            <Field label="priority">
              {(id) => (
                <Select
                  id={id}
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                  options={[
                    { value: "", label: "—" },
                    ...["P1", "P2", "P3", "P4"].map((p) => ({ value: p, label: p })),
                  ]}
                />
              )}
            </Field>
            <Field label="due">
              {(id) => (
                <TextInput id={id} type="date" value={due} onChange={(e) => setDue(e.target.value)} />
              )}
            </Field>
          </>
        ) : null}
        <Field label="realm">
          {(id) => (
            <Select
              id={id}
              value={realm}
              onChange={(e) => setRealm(e.target.value)}
              options={realms.map((r) => ({ value: r, label: r }))}
            />
          )}
        </Field>
      </div>

      {schedulable ? (
        <Field label="assignee">
          {(id) => (
            <Select
              id={id}
              value={assigneeValue}
              onChange={(e) => setAssignee(e.target.value)}
              options={[
                { value: "", label: "— unassigned —" },
                ...roster.map((p) => ({ value: p.id, label: p.title })),
                ...(currentAssignee && !matchedPerson
                  ? [{ value: UNRECOGNISED, label: `${props.assignee}  (not a person node)` }]
                  : []),
              ]}
            />
          )}
        </Field>
      ) : null}

      <Field label="body">
        {(id) => <TextArea id={id} value={body} onChange={(e) => setBody(e.target.value)} />}
      </Field>

      {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}

      <div className="ml-nodeedit__foot">
        <Button variant="primary" onClick={save} disabled={saving}>
          {saving ? "saving…" : "Save"}
        </Button>
        <Button onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </div>
  );
}
