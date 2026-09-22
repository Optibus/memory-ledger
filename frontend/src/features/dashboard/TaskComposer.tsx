import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { useMeta } from "@/api/MetaContext";
import { useDebounced } from "@/hooks/useDebounced";
import { Button, Checkbox, Field, Select, StatusMessage, TextArea, TextInput } from "@/components";
import type { LinkSuggestion } from "@/api/types";
import type { Facets } from "./useFacets";
import "./TaskComposer.css";

export interface TaskComposerProps {
  facets: Facets;
  /** If the filter has narrowed to exactly one realm, create there. */
  preferredRealm: string;
  onCreated: () => void;
  onCancel: () => void;
}

interface Pick extends LinkSuggestion {
  on: boolean;
  /** Set once the person re-points the relation, so typing never undoes it. */
  touched: boolean;
}

/**
 * The new-task form, with live link recognition.
 *
 * As you type the title the server scans it for nodes you already have and
 * reads the wording around each mention to guess the relation. It is a
 * suggestion engine, not an oracle — every row is re-pointable and uncheckable,
 * and your override survives further typing.
 */
export function TaskComposer({ facets, preferredRealm, onCreated, onCancel }: TaskComposerProps) {
  const { meta } = useMeta();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [due, setDue] = useState("");
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState("");
  const [type, setType] = useState("task");
  const [realm, setRealm] = useState(preferredRealm || facets.defaultRealm);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const debouncedTitle = useDebounced(title, 260);

  useEffect(() => {
    if (debouncedTitle.trim().length < 3) {
      setPicks([]);
      return;
    }
    const ctrl = new AbortController();
    api
      .suggestLinks(debouncedTitle, undefined, ctrl.signal)
      .then((r) => {
        setPicks((prev) => {
          const before = new Map(prev.map((p) => [p.id, p]));
          return (r.suggestions || []).map((s) => {
            const old = before.get(s.id);
            return {
              ...s,
              rel: old?.touched ? old.rel : s.rel,
              on: old ? old.on : true,
              touched: old?.touched ?? false,
            };
          });
        });
      })
      .catch(() => {
        /* recognition is a nicety; a failed guess must not block the form */
      });
    return () => ctrl.abort();
  }, [debouncedTitle]);

  const realmKeys = Object.keys(facets.realms).sort();
  if (!realmKeys.length && facets.defaultRealm) realmKeys.push(facets.defaultRealm);

  const submit = async () => {
    if (!title.trim()) {
      setError("title required");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.addTask({
        title: title.trim(),
        body: body.trim(),
        due: due ? `${due}T12:00:00` : "",
        assignee: assignee.trim(),
        priority,
        type,
        realm,
        links: picks.filter((p) => p.on).map((p) => ({ id: p.id, rel: p.rel })),
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(false);
    }
  };

  return (
    <div className="ml-composer">
      <Field label="title">
        {(id) => (
          <TextInput
            id={id}
            value={title}
            autoFocus
            placeholder="what needs doing? mention a ticket, PR or person and it links itself"
            onChange={(e) => setTitle(e.target.value)}
          />
        )}
      </Field>

      <div className="ml-composer__grid">
        <Field label="due">
          {(id) => <TextInput id={id} type="date" value={due} onChange={(e) => setDue(e.target.value)} />}
        </Field>
        <Field label="assignee">
          {(id) => (
            <>
              <TextInput
                id={id}
                list="ml-assignees"
                value={assignee}
                placeholder="me"
                onChange={(e) => setAssignee(e.target.value)}
              />
              <datalist id="ml-assignees">
                {Object.keys(facets.assignees).map((a) => (
                  <option key={a} value={a} />
                ))}
              </datalist>
            </>
          )}
        </Field>
        <Field label="priority">
          {(id) => (
            <Select
              id={id}
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              options={[{ value: "", label: "—" }, ...["P1", "P2", "P3", "P4"].map((p) => ({ value: p, label: p }))]}
            />
          )}
        </Field>
        <Field label="type">
          {(id) => (
            <Select
              id={id}
              value={type}
              onChange={(e) => setType(e.target.value)}
              options={[
                { value: "task", label: "task" },
                { value: "subtask", label: "subtask" },
              ]}
            />
          )}
        </Field>
        <Field label="realm">
          {(id) => (
            <Select
              id={id}
              value={realm}
              onChange={(e) => setRealm(e.target.value)}
              options={realmKeys.map((r) => ({ value: r, label: r }))}
            />
          )}
        </Field>
      </div>

      <Field label="details">
        {(id) => (
          <TextArea id={id} value={body} placeholder="notes (optional)" onChange={(e) => setBody(e.target.value)} />
        )}
      </Field>

      {picks.length ? (
        <div className="ml-composer__sug">
          <p className="ml-composer__sugh">
            recognised {picks.length} connection{picks.length === 1 ? "" : "s"} — edit or uncheck
          </p>
          {picks.map((p, i) => (
            <div className="ml-composer__sugrow" key={p.id}>
              <Checkbox
                checked={p.on}
                label={
                  <>
                    <b>{p.title.slice(0, 52)}</b> <span className="ml-composer__sugtype">({p.type})</span>
                  </>
                }
                onChange={(on) =>
                  setPicks((prev) => prev.map((x, j) => (j === i ? { ...x, on } : x)))
                }
              />
              <Select
                value={p.rel}
                aria-label={`relation to ${p.title}`}
                options={(meta?.rels ?? ["relates-to"]).map((r) => ({ value: r, label: r }))}
                onChange={(e) =>
                  setPicks((prev) =>
                    prev.map((x, j) => (j === i ? { ...x, rel: e.target.value, touched: true } : x)),
                  )
                }
              />
              {p.cue ? <span className="ml-composer__cue">matched &ldquo;{p.cue}&rdquo;</span> : null}
            </div>
          ))}
        </div>
      ) : null}

      {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}

      <div className="ml-composer__foot">
        <Button variant="primary" onClick={submit} disabled={saving}>
          {saving ? "saving…" : "Create task"}
        </Button>
        <Button onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </div>
  );
}
